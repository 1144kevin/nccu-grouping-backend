-- Membership-scoped history, durable completion and account-owned notification events.
create table public.core_chat_memberships (
 post_id uuid references public.core_posts(id) on delete cascade,
 member_id uuid references public.core_profiles(id) on delete cascade,
 joined_at timestamptz not null, left_at timestamptz,
 primary key(post_id,member_id)
);
create table public.core_order_completions (
 post_id uuid primary key references public.core_posts(id) on delete cascade,
 member_ids uuid[] not null, completed_at timestamptz not null default clock_timestamp()
);
create table public.core_notices (
 id uuid primary key default gen_random_uuid(), owner_id uuid not null references public.core_profiles(id) on delete cascade,
 kind text not null, thread_kind text not null, thread_id uuid not null,
 source_key text not null, created_at timestamptz not null default clock_timestamp(), read_at timestamptz,
 unique(owner_id,source_key)
);
create index core_notices_unread on public.core_notices(owner_id,created_at desc) where read_at is null;
create table public.core_push_devices (
 id uuid primary key, owner_id uuid not null references public.core_profiles(id) on delete cascade,
 proof_hash text not null check(proof_hash ~ '^[a-f0-9]{64}$'),
 expires_at timestamptz not null default clock_timestamp()+interval '30 days'
);
alter table public.core_chat_messages add column event_kind text check(event_kind in ('joined','left','completed'));
alter table public.core_chat_memberships enable row level security;
alter table public.core_order_completions enable row level security;
alter table public.core_notices enable row level security;
alter table public.core_push_devices enable row level security;
revoke all on public.core_chat_memberships,public.core_order_completions,public.core_notices,public.core_push_devices from public,anon,authenticated;

insert into public.core_chat_memberships(post_id,member_id,joined_at)
 select post_id,requester_id,coalesce(decided_at,created_at) from public.core_join_requests where status='accepted';
-- Snapshot previously completed real orders before replacing the mutable predicate.
insert into public.core_order_completions(post_id,member_ids)
 select p.id,array(select p.author_id union select requester_id from public.core_join_requests where post_id=p.id and status='accepted')
 from public.core_posts p where core_private.handoff_complete(p.id);

create function core_private.chat_visible_since(actor uuid,target uuid) returns timestamptz
language sql stable security definer set search_path='' as $$
 select case when p.author_id=actor then p.created_at else coalesce(
  (select joined_at from public.core_chat_memberships where post_id=target and member_id=actor and left_at is null),
  'infinity'::timestamptz) end from public.core_posts p where p.id=target;
$$;
revoke all on function core_private.chat_visible_since(uuid,uuid) from public,anon,authenticated;

create function core_private.notice_membership() returns trigger
language plpgsql security definer set search_path='' as $$
declare ts timestamptz:=clock_timestamp(); name text; host uuid;
begin
 select author_id into host from public.core_posts where id=new.post_id;
 select display_name into name from public.core_profiles where id=new.requester_id;
 if new.status='accepted' and (tg_op='INSERT' or old.status is distinct from 'accepted') then
  insert into public.core_chat_memberships(post_id,member_id,joined_at,left_at) values(new.post_id,new.requester_id,ts,null)
   on conflict(post_id,member_id) do update set joined_at=excluded.joined_at,left_at=null;
  insert into public.core_chat_messages(post_id,sender_id,content,client_key,created_at,event_kind)
   values(new.post_id,new.requester_id,name||' 已加入聊天室','membership-'||gen_random_uuid(),ts,'joined');
  insert into public.core_notices(owner_id,kind,thread_kind,thread_id,source_key)
   values(new.requester_id,'accepted','order',new.post_id,'accepted:'||new.id||':'||coalesce(new.decided_at,ts)) on conflict do nothing;
 elsif tg_op='UPDATE' and old.status='accepted' and new.status<>'accepted' then
  update public.core_chat_memberships set left_at=ts where post_id=new.post_id and member_id=new.requester_id;
  insert into public.core_chat_messages(post_id,sender_id,content,client_key,created_at,event_kind)
   values(new.post_id,new.requester_id,name||' 已退出聊天室','membership-'||gen_random_uuid(),ts,'left');
 end if;
 if new.status='pending' and (tg_op='INSERT' or old.status is distinct from 'pending') then
  insert into public.core_notices(owner_id,kind,thread_kind,thread_id,source_key)
   values(host,'join_request','order',new.post_id,'request:'||new.id||':'||ts) on conflict do nothing;
 elsif new.status<>'pending' then
  update public.core_notices set read_at=ts where owner_id=host and thread_id=new.post_id and kind='join_request' and read_at is null;
 end if;
 if new.status='cancelled' then
  update public.core_notices set read_at=ts where owner_id=new.requester_id and thread_id=new.post_id and kind='accepted' and read_at is null;
 end if;
 return new;
end $$;
revoke all on function core_private.notice_membership() from public,anon,authenticated;
create trigger core_membership_changes after insert or update of status on public.core_join_requests
 for each row execute function core_private.notice_membership();

create function core_private.notice_message() returns trigger
language plpgsql security definer set search_path='' as $$
declare recipients uuid[]; target uuid; k text; person uuid;
begin
 if tg_table_name='core_chat_messages' then
  target:=new.post_id; k:='group';
  select array(select author_id from public.core_posts where id=target union select requester_id from public.core_join_requests where post_id=target and status='accepted') into recipients;
 elsif tg_table_name='core_direct_messages' then
  target:=new.thread_id; k:='direct'; select array[host_id,guest_id] into recipients from public.core_direct_threads where id=target;
 else
  target:=new.proposal_id; k:='stealth'; select owners into recipients from public.core_stealth_proposals where id=target;
 end if;
 foreach person in array coalesce(recipients,'{}'::uuid[]) loop
  if person<>new.sender_id and not core_private.users_blocked(person,new.sender_id) then
   insert into public.core_notices(owner_id,kind,thread_kind,thread_id,source_key,created_at)
    values(person,'message',k,target,'message:'||new.id,new.created_at) on conflict do nothing;
  end if;
 end loop;
 return new;
end $$;
revoke all on function core_private.notice_message() from public,anon,authenticated;
create trigger core_group_notices after insert on public.core_chat_messages for each row execute function core_private.notice_message();
create trigger core_direct_notices after insert on public.core_direct_messages for each row execute function core_private.notice_message();
create trigger core_stealth_notices after insert on public.core_stealth_room_messages for each row execute function core_private.notice_message();

create function core_private.notice_read() returns trigger
language plpgsql security definer set search_path='' as $$
begin
 if tg_table_name='core_chat_reads' then
  update public.core_notices set read_at=clock_timestamp() where owner_id=new.viewer_id and thread_kind='group' and thread_id=new.post_id
   and kind='message' and created_at<=new.last_read_at and read_at is null;
 elsif new.read_at is not null then
  update public.core_notices set read_at=clock_timestamp() where owner_id=new.owner_id and thread_kind=new.kind and thread_id=new.thread_id
   and kind='message' and created_at<=new.read_at and read_at is null;
 end if;
 return new;
end $$;
revoke all on function core_private.notice_read() from public,anon,authenticated;
create trigger core_group_notice_read after insert or update on public.core_chat_reads for each row execute function core_private.notice_read();
create trigger core_thread_notice_read after insert or update of read_at on public.core_thread_preferences for each row execute function core_private.notice_read();

create or replace function core_private.handoff_complete(target uuid) returns boolean
language sql stable security definer set search_path='' as $$select exists(select 1 from public.core_order_completions where post_id=target);$$;

create function core_private.complete_order_if_ready() returns trigger
language plpgsql security definer set search_path='' as $$
declare p public.core_posts; members uuid[]; ts timestamptz:=clock_timestamp(); person uuid;
begin
 select * into p from public.core_posts where id=new.post_id for update;
 if p.cancelled or p.demo_batch is not null or not exists(select 1 from public.core_join_requests where post_id=p.id and status='accepted') then return new; end if;
 members:=array(select p.author_id union select requester_id from public.core_join_requests where post_id=p.id and status='accepted');
 if exists(select 1 from unnest(members) member where not exists(select 1 from public.core_handoff_receipts where post_id=p.id and member_id=member)) then return new; end if;
 insert into public.core_order_completions(post_id,member_ids,completed_at) values(p.id,members,ts) on conflict do nothing;
 if found then
  update public.core_posts set deadline_at=least(deadline_at,ts),payload=jsonb_set(payload,'{deadline_at}',to_jsonb(least(deadline_at,ts))),version=version+1 where id=p.id;
  insert into public.core_chat_messages(post_id,sender_id,content,client_key,event_kind) values(p.id,p.author_id,'全員已確認完成訂單，信譽積分已更新。','completed-'||p.id,'completed');
  foreach person in array members loop
   insert into public.core_notices(owner_id,kind,thread_kind,thread_id,source_key) values(person,'completed','order',p.id,'completed:'||p.id) on conflict do nothing;
  end loop;
 end if;
 return new;
end $$;
revoke all on function core_private.complete_order_if_ready() from public,anon,authenticated;
create trigger core_complete_order after insert on public.core_handoff_receipts for each row execute function core_private.complete_order_if_ready();

create function public.core_notifications(action text,args jsonb default '{}') returns jsonb
language plpgsql security definer set search_path='' as $$
declare actor uuid:=core_private.account_guard(); target uuid;
begin
 if action='read_orders' and args-array['post_id']='{}' then
  target:=(args->>'post_id')::uuid;
  update public.core_notices set read_at=clock_timestamp() where owner_id=actor and kind in ('accepted','completed') and read_at is null and (target is null or thread_id=target);
  return jsonb_build_object('ok',true);
 end if;
 if action='device' and args-array['id','proof_hash','remove']='{}' then
  target:=(args->>'id')::uuid;
  if coalesce((args->>'remove')::boolean,false) then delete from public.core_push_devices where id=target and owner_id=actor;
  else
   if args->>'proof_hash' !~ '^[a-f0-9]{64}$' or args->>'proof_hash' is null then raise exception using errcode='PT422',message='invalid_device'; end if;
   if exists(select 1 from public.core_push_devices where id=target and owner_id<>actor) then raise exception using errcode='PT409',message='device_account_changed'; end if;
   if (select count(*) from public.core_push_devices where owner_id=actor and expires_at>clock_timestamp())>=6 and not exists(select 1 from public.core_push_devices where id=target and owner_id=actor) then raise exception using errcode='PT429',message='too_many_devices'; end if;
   insert into public.core_push_devices(id,owner_id,proof_hash) values(target,actor,args->>'proof_hash')
    on conflict(id) do update set proof_hash=excluded.proof_hash,expires_at=clock_timestamp()+interval '30 days';
  end if;
  return jsonb_build_object('ok',true);
 end if;
 raise exception using errcode='PT422',message='invalid_notification_input';
end $$;
revoke all on function public.core_notifications(text,jsonb) from public,anon,authenticated;
grant execute on function public.core_notifications(text,jsonb) to authenticated;

-- A random device capability reveals only a generic notification count and route.
-- It grants no access to accounts, chats, posts, content, names, or mutations.
create function public.core_push_snapshot(device_id uuid,proof text) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='3s' as $$
declare actor uuid; prefs jsonb; latest public.core_notices; unread integer;
begin
 if length(proof)<>43 then raise exception using errcode='PT404',message='device_not_found'; end if;
 select owner_id into actor from public.core_push_devices where id=device_id and proof_hash=encode(sha256(convert_to(proof,'UTF8')),'hex') and expires_at>clock_timestamp();
 if actor is null or core_private.account_pending(actor) then raise exception using errcode='PT404',message='device_not_found'; end if;
 select payload->'notifications' into prefs from public.core_account_preferences where owner_id=actor;
 select count(*) into unread from public.core_notices n where owner_id=actor and read_at is null
  and coalesce((prefs->>case when n.kind='message' then 'messages' else 'joinRequests' end)::boolean,true)
  and not exists(select 1 from public.core_thread_preferences where owner_id=actor and kind=n.thread_kind and thread_id=n.thread_id and muted);
 select * into latest from public.core_notices n where owner_id=actor and read_at is null
  and coalesce((prefs->>case when n.kind='message' then 'messages' else 'joinRequests' end)::boolean,true)
  and not exists(select 1 from public.core_thread_preferences where owner_id=actor and kind=n.thread_kind and thread_id=n.thread_id and muted)
  order by created_at desc,id desc limit 1;
 return jsonb_build_object('count',unread,'id',latest.id,'created_at',latest.created_at,'kind',latest.kind,
  'url',case latest.thread_kind when 'order' then '/order/' when 'group' then '/chat/' when 'direct' then '/direct-chat/' else '/stealth-chat/' end||latest.thread_id);
end $$;
revoke all on function public.core_push_snapshot(uuid,text) from public,anon,authenticated;
grant execute on function public.core_push_snapshot(uuid,text) to anon;

-- Preserve the existing session, moderation, paging and permission boundaries.
CREATE OR REPLACE FUNCTION core_private.chat(action text, args jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
 SET statement_timeout TO '8s'
AS $function$
<<chat>>
declare actor uuid:=auth.uid(); post public.core_posts; message public.core_chat_messages;
  page_size integer; value jsonb; result jsonb; next_cursor text; last_time timestamptz; last_id uuid;
  content text; client_key text; allowed text[];
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false)) or
    not exists(select 1 from auth.sessions where id=(auth.jwt()->>'session_id')::uuid and user_id=actor
      and (not_after is null or not_after>clock_timestamp()))
  then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  allowed:=case action when 'list' then array['limit','cursor']
    when 'messages' then array['post_id','limit','cursor']
    when 'send' then array['post_id','content','client_key'] else null end;
  if allowed is null or jsonb_typeof(args) is distinct from 'object' or args-allowed<>'{}'::jsonb then
    raise exception using errcode='PT422',message='invalid_chat_input'; end if;
  page_size:=coalesce((args->>'limit')::integer,30);
  if page_size not between 1 and 50 then raise exception using errcode='PT422',message='invalid_query'; end if;
  if coalesce(args->>'cursor','')<>'' then
    value:=convert_from(decode(args->>'cursor','base64'),'UTF8')::jsonb;
    if jsonb_typeof(value) is distinct from 'array' or jsonb_array_length(value)<>2 then
      raise exception using errcode='PT422',message='invalid_cursor'; end if;
    last_time:=(value->>0)::timestamptz; last_id:=(value->>1)::uuid;
    if last_time is null or last_id is null then raise exception using errcode='PT422',message='invalid_cursor'; end if;
  end if;
  if action='list' then
    select coalesce(jsonb_agg(jsonb_build_object('post',core_private.post_view(p.id,actor),'last_message',(
      select core_private.chat_message_view(m) from public.core_chat_messages m where m.post_id=p.id and m.created_at>=core_private.chat_visible_since(actor,p.id)
      order by m.created_at desc,m.id desc limit 1)) order by p.created_at desc,p.id desc),'[]') into result
    from (
      select p.* from public.core_posts p where (p.author_id=actor or exists (
        select 1 from public.core_join_requests j where j.post_id=p.id and j.requester_id=actor and j.status='accepted'))
      and (last_time is null or (p.created_at,p.id)<(last_time,last_id))
      order by p.created_at desc,p.id desc limit page_size+1
    ) p;
    if jsonb_array_length(result)>page_size then
      value:=result->(page_size-1)->'post';
      next_cursor:=replace(encode(convert_to(jsonb_build_array(value->>'created_at',value->>'id')::text,'UTF8'),'base64'),E'\n','');
      result:=result-page_size;
    end if;
    return jsonb_build_object('items',result,'next_cursor',next_cursor);
  end if;
  if action='send' then
    -- Serializes each sender's quota and idempotency across different groups.
    perform 1 from public.core_profiles where id=actor for update;
  end if;
  if action='send' then
    select * into post from public.core_posts where id=(args->>'post_id')::uuid for update;
  else
    select * into post from public.core_posts where id=(args->>'post_id')::uuid;
  end if;
  if not found then raise exception using errcode='PT404',message='post_not_found'; end if;
  if post.author_id<>actor and not exists(select 1 from public.core_join_requests
    where post_id=post.id and requester_id=actor and status='accepted')
  then raise exception using errcode='PT403',message='chat_members_only'; end if;
  if action='send' then
    if jsonb_typeof(args->'content') is distinct from 'string' or
      jsonb_typeof(args->'client_key') is distinct from 'string' then
      raise exception using errcode='PT422',message='invalid_chat_input'; end if;
    content:=btrim(args->>'content'); client_key:=args->>'client_key';
    if char_length(content) not between 1 and 2000 or char_length(client_key) not between 12 and 100 then
      raise exception using errcode='PT422',message='invalid_chat_input'; end if;
    select * into message from public.core_chat_messages where sender_id=actor and core_chat_messages.client_key=chat.client_key;
    if found then
      if message.post_id<>post.id or message.content<>content then
        raise exception using errcode='PT409',message='message_key_payload_conflict'; end if;
      return core_private.chat_message_view(message);
    end if;
    if post.cancelled then raise exception using errcode='PT409',message='chat_read_only'; end if;
    if (select count(*) from public.core_chat_messages where sender_id=actor and created_at>clock_timestamp()-interval '1 minute')>=30 then
      raise exception using errcode='PT429',message='chat_rate_limit'; end if;
    insert into public.core_chat_messages(post_id,sender_id,content,client_key)
      values(post.id,actor,content,client_key) returning * into message;
    return core_private.chat_message_view(message);
  end if;
  select coalesce(jsonb_agg(core_private.chat_message_view(m) order by m.created_at asc,m.id asc),'[]') into result from (
    select m.* from public.core_chat_messages m where m.post_id=post.id and m.created_at>=core_private.chat_visible_since(actor,post.id)
    and (last_time is null or (m.created_at,m.id)<(last_time,last_id))
    order by m.created_at desc,m.id desc limit page_size+1
  ) m;
  if jsonb_array_length(result)>page_size then
    result:=result-0;
    value:=result->0;
    next_cursor:=replace(encode(convert_to(jsonb_build_array(value->>'created_at',value->>'id')::text,'UTF8'),'base64'),E'\n','');
  end if;
  return jsonb_build_object('post',core_private.post_view(post.id,actor),'messages',result,'next_cursor',next_cursor,'can_send',not post.cancelled);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range
  or character_not_in_repertoire or datetime_field_overflow or invalid_datetime_format then
  raise exception using errcode='PT422',message='invalid_chat_input';
end;
$function$;

CREATE OR REPLACE FUNCTION core_private.visible_unread(actor uuid, target uuid)
 RETURNS integer
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
 select count(*)::integer from public.core_chat_messages m
 left join public.core_chat_reads r on r.post_id=m.post_id and r.viewer_id=actor
 where m.post_id=target and m.created_at>=core_private.chat_visible_since(actor,target) and m.sender_id<>actor and not core_private.users_blocked(actor,m.sender_id)
 and (r.last_read_at is null or (m.created_at,m.id)>(r.last_read_at,r.last_read_id));
$function$;

CREATE OR REPLACE FUNCTION core_private.social_thread_before_unified(k text, target uuid, actor uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare p public.core_posts; d public.core_direct_threads; pref public.core_thread_preferences;
 legacy public.core_inbox_preferences; msg jsonb; last_at timestamptz; unread integer; peer uuid;
begin
 if k='direct' then
  select * into d from public.core_direct_threads where id=target and actor in (host_id,guest_id);
  if not found or core_private.users_blocked(d.host_id,d.guest_id) then return null; end if;
  select * into p from public.core_posts where id=d.post_id;
  peer:=case when actor=d.host_id then d.guest_id else d.host_id end;
 else
  select * into p from public.core_posts where id=target and (author_id=actor or exists(
   select 1 from public.core_join_requests j where j.post_id=target and j.requester_id=actor and j.status='accepted'));
  if not found then return null; end if;
  peer:=p.author_id;
  select * into legacy from public.core_inbox_preferences where owner_id=actor and post_id=target;
 end if;
 if p.id is null or not core_private.post_accessible(actor,p.id) then return null; end if;
 select * into pref from public.core_thread_preferences where owner_id=actor and kind=k and thread_id=target;
 if k='direct' then
  select core_private.social_message(m),m.created_at into msg,last_at from public.core_direct_messages m
   where m.thread_id=target order by m.created_at desc,m.id desc limit 1;
  select count(*)::integer into unread from public.core_direct_messages m where m.thread_id=target
   and m.sender_id<>actor and (pref.read_at is null or m.created_at>pref.read_at);
 else
  select core_private.chat_message_view(m),m.created_at into msg,last_at from public.core_chat_messages m
   where m.post_id=target and m.created_at>=core_private.chat_visible_since(actor,target) and not m.moderation_hidden and not core_private.users_blocked(actor,m.sender_id)
   order by m.created_at desc,m.id desc limit 1;
  unread:=core_private.visible_unread(actor,target);
 end if;
 last_at:=coalesce(last_at,p.created_at);
 return jsonb_build_object('id',target,'kind',k,'post',core_private.post_view(p.id,actor),'peer_id',peer,'peer_level',core_private.reputation_level(peer),
  'peer_name',(select display_name from public.core_profiles where id=peer),
  'peer_badge',(select badge from public.core_profiles where id=peer),
  'last_message',msg,'updated_at',last_at,'unread_count',case when coalesce(pref.muted,legacy.muted,false) then 0 else unread end,
  'pinned',coalesce(pref.pinned,false),'inbox',coalesce(pref.inbox,'general'),
  'muted',coalesce(pref.muted,legacy.muted,false),
  'deleted',case when pref.owner_id is not null then pref.hidden_at is not null and last_at<=pref.hidden_at else coalesce(legacy.archived,false) end);
end $function$;

CREATE OR REPLACE FUNCTION core_private.chat_photo_access(photo core_chat_photos, actor uuid)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare value jsonb;
begin
 if actor is distinct from core_private.media_actor() or core_private.account_pending(actor)
  or core_private.users_blocked(actor,photo.owner_id) then return false; end if;
 if photo.kind='group' then
  if not exists(select 1 from public.core_chat_messages where id=photo.message_id and post_id=photo.thread_id and created_at>=core_private.chat_visible_since(actor,photo.thread_id) and not moderation_hidden) then return false; end if;
  return core_private.social_thread('group',photo.thread_id,actor) is not null;
 elsif photo.kind='direct' then
  return exists(select 1 from public.core_direct_messages where id=photo.message_id and thread_id=photo.thread_id)
   and core_private.social_thread('direct',photo.thread_id,actor) is not null;
 end if;
 value:=public.core_stealth_quick('state',jsonb_build_object('proposal_id',photo.thread_id));
 return (value->>'opened')::boolean and exists(select 1 from public.core_stealth_room_messages where id=photo.message_id and proposal_id=photo.thread_id);
exception when sqlstate 'PT404' or sqlstate 'PT403' or sqlstate 'PT401' then return false;
end $function$;

CREATE OR REPLACE FUNCTION core_private.search(args jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
 SET statement_timeout TO '8s'
AS $function$
declare actor uuid:=auth.uid(); scope text; keyword text; page_size integer;
  result jsonb; value jsonb; last_time timestamptz; last_id uuid; next_cursor text;
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false))
    or not exists(select 1 from auth.sessions where id=(auth.jwt()->>'session_id')::uuid and user_id=actor
      and (not_after is null or not_after>clock_timestamp())) then
    raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  if jsonb_typeof(args) is distinct from 'object' or args-array['scope','q','limit','cursor']<>'{}'::jsonb
    or jsonb_typeof(args->'scope') is distinct from 'string' or jsonb_typeof(args->'q') is distinct from 'string' then
    raise exception using errcode='PT422',message='invalid_search_query'; end if;
  scope:=args->>'scope'; keyword:=btrim(args->>'q'); page_size:=coalesce((args->>'limit')::integer,30);
  if scope not in ('users','messages','history') or char_length(keyword) not between 2 and 80
    or page_size not between 1 and 50 then
    raise exception using errcode='PT422',message='invalid_search_query'; end if;
  if coalesce(args->>'cursor','')<>'' then
    value:=convert_from(decode(args->>'cursor','base64'),'UTF8')::jsonb;
    if jsonb_typeof(value) is distinct from 'array' or jsonb_array_length(value)<>5
      or value->>0 is distinct from scope or value->>1 is distinct from keyword
      or value->>2 is distinct from actor::text then
      raise exception using errcode='PT422',message='invalid_search_cursor'; end if;
    last_time:=(value->>3)::timestamptz; last_id:=(value->>4)::uuid;
    if last_time is null or last_id is null then raise exception using errcode='PT422',message='invalid_search_cursor'; end if;
  end if;
  if scope='users' then
    select coalesce(jsonb_agg(jsonb_build_object('id',p.id,'kind','user','title',p.display_name,
      'detail',p.bio,'created_at',p.created_at) order by p.created_at desc,p.id desc),'[]') into result
    from (select * from public.core_profiles where
      (position(lower(keyword) in lower(display_name))>0 or position(lower(keyword) in lower(bio))>0)
      and (last_time is null or (created_at,id)<(last_time,last_id))
      order by created_at desc,id desc limit page_size+1) p;
  elsif scope='messages' then
    select coalesce(jsonb_agg(jsonb_build_object('id',m.id,'kind','message','title',sender.display_name,
      'detail',left(m.content,300),'post_id',m.post_id,'created_at',m.created_at)
      order by m.created_at desc,m.id desc),'[]') into result
    from (select cm.* from public.core_chat_messages cm join public.core_posts p on p.id=cm.post_id
      where (p.author_id=actor or exists(select 1 from public.core_join_requests j
        where j.post_id=p.id and j.requester_id=actor and j.status='accepted'))
      and cm.created_at>=core_private.chat_visible_since(actor,cm.post_id) and not cm.moderation_hidden and position(lower(keyword) in lower(cm.content))>0
      and (last_time is null or (cm.created_at,cm.id)<(last_time,last_id))
      order by cm.created_at desc,cm.id desc limit page_size+1) m
      join public.core_profiles sender on sender.id=m.sender_id;
  else
    select coalesce(jsonb_agg(jsonb_build_object('id',p.id,'kind','history','title',left(p.payload->>'content',300),
      'detail',case when p.cancelled then '已取消' else '已截止' end,'post_id',p.id,'created_at',p.created_at)
      order by p.created_at desc,p.id desc),'[]') into result
    from (select cp.* from public.core_posts cp join public.core_profiles author on author.id=cp.author_id
      where (cp.author_id=actor or exists(select 1 from public.core_join_requests j where j.post_id=cp.id and j.requester_id=actor))
      and (cp.cancelled or cp.deadline_at<=clock_timestamp())
      and (position(lower(keyword) in lower(cp.payload->>'content'))>0
        or position(lower(keyword) in lower(author.display_name))>0
        or exists(select 1 from jsonb_array_elements_text(cp.payload->'tags') tag where position(lower(keyword) in lower(tag))>0))
      and (last_time is null or (cp.created_at,cp.id)<(last_time,last_id))
      order by cp.created_at desc,cp.id desc limit page_size+1) p;
  end if;
  if jsonb_array_length(result)>page_size then
    value:=result->(page_size-1);
    next_cursor:=replace(encode(convert_to(jsonb_build_array(scope,keyword,actor::text,value->>'created_at',value->>'id')::text,'UTF8'),'base64'),E'\n','');
    result:=result-page_size;
  end if;
  
 select coalesce(jsonb_agg(item || coalesce((select jsonb_build_object('level',core_private.reputation_level(p.id),'badge',p.badge)
 from public.core_profiles p where p.id=case when item->>'kind'='user' then (item->>'id')::uuid
 when item->>'kind'='message' then (select sender_id from public.core_chat_messages where id=(item->>'id')::uuid) end),'{}'::jsonb) order by ordinal),'[]'::jsonb)
 into result from jsonb_array_elements(result) with ordinality entries(item,ordinal);
 return jsonb_build_object('items',result,'next_cursor',next_cursor);
 
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range
  or character_not_in_repertoire or datetime_field_overflow or invalid_datetime_format then
  raise exception using errcode='PT422',message='invalid_search_query';
end;
$function$;

CREATE OR REPLACE FUNCTION core_private.chat_activity(action text, args jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
 SET statement_timeout TO '8s'
AS $function$
declare actor uuid:=auth.uid(); post public.core_posts; message public.core_chat_messages;
  result jsonb; total integer; pending integer; allowed text[];
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false)) or
    not exists(select 1 from auth.sessions where id=(auth.jwt()->>'session_id')::uuid and user_id=actor
      and (not_after is null or not_after>clock_timestamp()))
  then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  allowed:=case action when 'status' then array[]::text[] when 'read' then array['post_id','message_id'] else null end;
  if allowed is null or jsonb_typeof(args) is distinct from 'object' or args-allowed<>'{}'::jsonb then
    raise exception using errcode='PT422',message='invalid_chat_input'; end if;
  if action='read' then
    select * into post from public.core_posts where id=(args->>'post_id')::uuid;
    if not found then raise exception using errcode='PT404',message='post_not_found'; end if;
    if post.author_id<>actor and not exists(select 1 from public.core_join_requests
      where post_id=post.id and requester_id=actor and status='accepted')
    then raise exception using errcode='PT403',message='chat_members_only'; end if;
    select * into message from public.core_chat_messages where id=(args->>'message_id')::uuid and post_id=post.id and created_at>=core_private.chat_visible_since(actor,post.id);
    if not found then raise exception using errcode='PT404',message='message_not_found'; end if;
    -- Use the displayed message's database timestamp, never a client-supplied clock.
    insert into public.core_chat_reads(post_id,viewer_id,last_read_at,last_read_id)
      values(post.id,actor,message.created_at,message.id)
      on conflict(post_id,viewer_id) do update set last_read_at=excluded.last_read_at,
        last_read_id=excluded.last_read_id,updated_at=clock_timestamp()
      where (core_chat_reads.last_read_at,core_chat_reads.last_read_id)<(excluded.last_read_at,excluded.last_read_id);
    return jsonb_build_object('ok',true);
  end if;
  select coalesce(jsonb_agg(jsonb_build_object('post_id',p.id,'unread_count',counts.n)),'[]'),
    coalesce(sum(counts.n),0)::integer into result,total
  from public.core_posts p
  left join public.core_chat_reads r on r.post_id=p.id and r.viewer_id=actor
  cross join lateral (
    select count(*)::integer n from public.core_chat_messages m where m.post_id=p.id and m.created_at>=core_private.chat_visible_since(actor,p.id) and m.sender_id<>actor
      and (r.last_read_at is null or (m.created_at,m.id)>(r.last_read_at,r.last_read_id))
  ) counts
  where p.author_id=actor or exists(select 1 from public.core_join_requests j
    where j.post_id=p.id and j.requester_id=actor and j.status='accepted');
  select count(*)::integer into pending from public.core_join_requests j join public.core_posts p on p.id=j.post_id
    where p.author_id=actor and not p.cancelled and p.deadline_at>clock_timestamp() and j.status='pending';
  return jsonb_build_object('groups',result,'total_unread',total,'pending_requests',pending,'accepted_requests',(select count(*) from public.core_notices where owner_id=actor and kind='accepted' and read_at is null));
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range then
  raise exception using errcode='PT422',message='invalid_chat_input';
end;
$function$;

CREATE OR REPLACE FUNCTION core_private.social(action text, args jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
 SET statement_timeout TO '8s'
AS $function$
declare actor uuid:=core_private.account_guard(); target uuid; k text; value jsonb; result jsonb; items jsonb; c jsonb;
 allowed text[]; page_size integer; last_msg public.core_direct_messages; d public.core_direct_threads; p public.core_posts;
 text_value text; key_value text; unread integer:=0; done integer; members jsonb; is_member boolean;
begin
 allowed:=case action
 when 'list' then array['kind','limit','cursor','deleted'] when 'preference' then array['kind','thread_id','pinned','inbox','deleted','muted']
 when 'start' then array['post_id'] when 'messages' then array['thread_id','limit','cursor']
 when 'send' then array['thread_id','content','client_key'] when 'read' then array['thread_id','message_id']
 when 'order' then array['post_id'] when 'handoff' then array['post_id'] when 'experience' then array[]::text[] else null end;
 if allowed is null or jsonb_typeof(args) is distinct from 'object' or args-allowed<>'{}' then
  raise exception using errcode='PT422',message='invalid_social_input'; end if;
 if action='experience' then
  select count(*)::integer into done from public.core_handoff_receipts r where r.member_id=actor and core_private.handoff_complete(r.post_id);
  return jsonb_build_object('score',least(100,done*10),'level',greatest(1,least(5,(greatest(1,done*10)+19)/20)),
   'completed_orders',done,'completed_post_ids',(select coalesce(jsonb_agg(r.post_id),'[]'::jsonb) from public.core_handoff_receipts r where r.member_id=actor and core_private.handoff_complete(r.post_id)),'history_count',(select count(*) from public.core_posts pp where (pp.author_id=actor or exists(select 1 from public.core_join_requests j where j.post_id=pp.id and j.requester_id=actor)) and (pp.cancelled or pp.deadline_at<=clock_timestamp())));
 end if;
 if action in ('order','handoff') then
  target:=(args->>'post_id')::uuid;
  -- Reuse the existing visibility/session boundary before exposing membership.
  value:=public.core_dispatch('post',jsonb_build_object('post_id',target));
  select * into p from public.core_posts where id=target for update;
  is_member:=p.author_id=actor or exists(select 1 from public.core_join_requests where post_id=target and requester_id=actor and status='accepted');
  if action='handoff' then
   if not is_member then raise exception using errcode='PT403',message='chat_members_only'; end if;
   if p.cancelled or p.demo_batch is not null or not exists(select 1 from public.core_join_requests where post_id=target and status='accepted') then
    raise exception using errcode='PT409',message='handoff_not_ready'; end if;
   insert into public.core_handoff_receipts(post_id,member_id) values(target,actor) on conflict do nothing;
  end if;
  select coalesce(jsonb_agg(m.info order by m.host desc,m.id),'[]') into members from (
   select p.author_id id,true host,jsonb_build_object('id',p.author_id,'name',pr.display_name,'badge',pr.badge,'level',core_private.reputation_level(pr.id),'host',true) info from public.core_profiles pr where pr.id=p.author_id
   union all select j.requester_id,false,jsonb_build_object('id',j.requester_id,'name',pr.display_name,'badge',pr.badge,'level',core_private.reputation_level(pr.id),'host',false)
    from public.core_join_requests j join public.core_profiles pr on pr.id=j.requester_id where j.post_id=target and j.status='accepted' and (is_member or j.requester_id=actor)
  ) m;
  return jsonb_build_object('post',value,'members',members,'formed',(value->>'joined')::integer>1,
   'completed',coalesce(core_private.handoff_complete(target),false),
   'confirmed_count',(select count(*) from public.core_handoff_receipts where post_id=target),
   'my_confirmed',exists(select 1 from public.core_handoff_receipts where post_id=target and member_id=actor),
   'can_confirm',is_member and not p.cancelled and p.demo_batch is null and (value->>'joined')::integer>1);
 end if;
 if action='list' then
  k:=coalesce(args->>'kind','all'); page_size:=coalesce((args->>'limit')::integer,30);
  if k not in ('all','primary','group','direct') or page_size not between 1 and 50 or (args ? 'deleted' and jsonb_typeof(args->'deleted')<>'boolean') then
   raise exception using errcode='PT422',message='invalid_social_input'; end if;
  if coalesce(args->>'cursor','')<>'' then
   c:=(args->>'cursor')::jsonb;
   if c->>'owner' is distinct from actor::text or c->>'kind' is distinct from k or c->>'deleted' is distinct from coalesce(args->>'deleted','false') then
    raise exception using errcode='PT422',message='invalid_social_cursor'; end if;
  end if;
  with candidates as (
   select 'group' kind,pp.id from public.core_posts pp where k in ('all','primary','group') and (pp.author_id=actor or exists(select 1 from public.core_join_requests j where j.post_id=pp.id and j.requester_id=actor and j.status='accepted'))
   union all select 'direct',dd.id from public.core_direct_threads dd where k in ('all','primary','direct') and actor in (dd.host_id,dd.guest_id)
   union all select 'stealth',sp.id from public.core_stealth_proposals sp where k in ('all','primary','group') and actor=any(sp.owners)
  ), visible as (
   select core_private.social_thread(kind,id,actor) item from candidates
  ), all_items as (
   select item from visible where item is not null and (k<>'primary' or item->>'inbox'='primary') and (item->>'deleted')::boolean=coalesce((args->>'deleted')::boolean,false)
  ), page as (
   select item from all_items where c is null or ((item->>'pinned')::boolean,(item->>'updated_at')::timestamptz,item->>'id',item->>'kind')<((c->>'pinned')::boolean,(c->>'at')::timestamptz,c->>'id',c->>'thread_kind')
   order by (item->>'pinned')::boolean desc,(item->>'updated_at')::timestamptz desc,item->>'id' desc,item->>'kind' desc limit page_size+1
  ) select coalesce(jsonb_agg(item order by (item->>'pinned')::boolean desc,(item->>'updated_at')::timestamptz desc,item->>'id' desc,item->>'kind' desc),'[]'),(select coalesce(sum((item->>'unread_count')::integer),0) from all_items) into items,unread from page;
  value:=null;
  if jsonb_array_length(items)>page_size then
   items:=items-page_size; result:=items->(page_size-1);
   value:=to_jsonb(jsonb_build_object('owner',actor,'kind',k,'deleted',coalesce(args->>'deleted','false'),'pinned',result->'pinned','at',result->'updated_at','id',result->>'id','thread_kind',result->>'kind')::text);
  end if;
  return jsonb_build_object('items',items,'next_cursor',value,'total_unread',unread);
 end if;
 if action='start' then
  target:=(args->>'post_id')::uuid;
  value:=public.core_dispatch('post',jsonb_build_object('post_id',target));
  select * into p from public.core_posts where id=target for key share;
  if p.id is null or p.author_id=actor or core_private.users_blocked(actor,p.author_id) then
   raise exception using errcode='PT403',message='direct_message_unavailable'; end if;
  if (select count(*) from public.core_direct_threads where guest_id=actor and created_at>clock_timestamp()-interval '1 minute')>=10 then
   raise exception using errcode='PT429',message='direct_message_rate_limit'; end if;
  insert into public.core_direct_threads(post_id,host_id,guest_id) values(target,p.author_id,actor) on conflict(post_id,guest_id) do update set post_id=excluded.post_id returning * into d;
  insert into public.core_thread_preferences(owner_id,kind,thread_id) values(actor,'direct',d.id) on conflict do nothing;
  update public.core_thread_preferences set hidden_at=null where owner_id=actor and kind='direct' and thread_id=d.id;
  return core_private.social_thread('direct',d.id,actor);
 end if;
 target:=(args->>'thread_id')::uuid; k:=case when action='preference' then args->>'kind' else 'direct' end;
 if k is null or k not in ('group','direct','stealth') or target is null then raise exception using errcode='PT422',message='invalid_social_input'; end if;
 value:=core_private.social_thread(k,target,actor);
 if value is null then raise exception using errcode='PT404',message='conversation_not_found'; end if;
 if action='preference' then
  if (args ? 'inbox' and args->>'inbox' not in ('primary','general')) or exists(select 1 from jsonb_each(args) x where x.key in ('pinned','deleted','muted') and jsonb_typeof(x.value)<>'boolean') then
   raise exception using errcode='PT422',message='invalid_social_input'; end if;
  insert into public.core_thread_preferences(owner_id,kind,thread_id,pinned,inbox,muted) values(actor,k,target,(value->>'pinned')::boolean,value->>'inbox',(value->>'muted')::boolean) on conflict do nothing;
  update public.core_thread_preferences set pinned=coalesce((args->>'pinned')::boolean,pinned),inbox=coalesce(args->>'inbox',inbox),muted=coalesce((args->>'muted')::boolean,muted),
   hidden_at=case when args ? 'deleted' then case when (args->>'deleted')::boolean then clock_timestamp() else null end else hidden_at end
   where owner_id=actor and kind=k and thread_id=target;
  return core_private.social_thread(k,target,actor);
 end if;
 select * into d from public.core_direct_threads where id=target for update;
 if action='send' then
  text_value:=btrim(args->>'content'); key_value:=args->>'client_key';
  if jsonb_typeof(args->'content') is distinct from 'string' or char_length(text_value) not between 1 and 2000 or char_length(key_value) not between 12 and 100 or key_value is null then
   raise exception using errcode='PT422',message='invalid_chat_input'; end if;
  select * into last_msg from public.core_direct_messages where sender_id=actor and client_key=key_value;
  if found then
   if last_msg.thread_id<>target or last_msg.content<>text_value then raise exception using errcode='PT409',message='message_key_payload_conflict'; end if;
   return core_private.social_message(last_msg);
  end if;
  if (select count(*) from public.core_direct_messages where sender_id=actor and created_at>clock_timestamp()-interval '1 minute')>=30 then
   raise exception using errcode='PT429',message='direct_message_rate_limit'; end if;
  insert into public.core_direct_messages(thread_id,sender_id,content,client_key) values(target,actor,text_value,key_value) returning * into last_msg;
  return core_private.social_message(last_msg);
 elsif action='read' then
  select * into last_msg from public.core_direct_messages where id=(args->>'message_id')::uuid and thread_id=target;
  if not found then raise exception using errcode='PT404',message='message_not_found'; end if;
  insert into public.core_thread_preferences(owner_id,kind,thread_id,read_at) values(actor,'direct',target,last_msg.created_at) on conflict(owner_id,kind,thread_id)
   do update set read_at=greatest(core_thread_preferences.read_at,excluded.read_at);
  return jsonb_build_object('ok',true);
 end if;
 page_size:=coalesce((args->>'limit')::integer,30);
 if page_size not between 1 and 50 then raise exception using errcode='PT422',message='invalid_social_input'; end if;
 if coalesce(args->>'cursor','')<>'' then
  c:=(args->>'cursor')::jsonb;
  if c->>'owner' is distinct from actor::text or c->>'thread' is distinct from target::text then raise exception using errcode='PT422',message='invalid_social_cursor'; end if;
 end if;
 select coalesce(jsonb_agg(core_private.social_message(m) order by m.created_at desc,m.id desc),'[]') into items from (
  select * from public.core_direct_messages m where thread_id=target and (c is null or (m.created_at,m.id)<((c->>'at')::timestamptz,(c->>'id')::uuid)) order by created_at desc,id desc limit page_size+1
 ) m;
 result:=null;
 if jsonb_array_length(items)>page_size then
  items:=items-page_size; result:=to_jsonb(jsonb_build_object('owner',actor,'thread',target,'at',items->(page_size-1)->>'created_at','id',items->(page_size-1)->>'id')::text);
 end if;
 return jsonb_build_object('thread',value,'messages',items,'next_cursor',result,'can_send',true);
exception when invalid_text_representation or numeric_value_out_of_range or invalid_parameter_value or datetime_field_overflow then
 raise exception using errcode='PT422',message='invalid_social_input';
end $function$;

CREATE OR REPLACE FUNCTION core_private.dispatch(action text, args jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
 SET statement_timeout TO '8s'
AS $function$
declare actor uuid := auth.uid(); post public.core_posts; request public.core_join_requests;
  v_payload jsonb; result jsonb; target_post_id uuid; n integer; expected integer; profile public.core_profiles;
  scope text; term text; last_time timestamptz; last_id uuid; page_size integer; next_cursor text; allowed text[];
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false)) or
    not exists(select 1 from auth.sessions where id=(auth.jwt()->>'session_id')::uuid and user_id=actor
      and (not_after is null or not_after>clock_timestamp()))
  then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  if jsonb_typeof(args) is distinct from 'object' then
    raise exception using errcode='PT422',message='invalid_input'; end if;
  if action in ('join','withdraw','cancel','edit','decide') and exists(select 1 from public.core_order_completions where post_id=case when action='decide' then (select post_id from public.core_join_requests where id=(args->>'join_id')::uuid) else (args->>'post_id')::uuid end) then raise exception using errcode='PT409',message='order_already_completed'; end if;
  allowed := case action
    when 'me' then array[]::text[] when 'profile' then array['display_name','bio','badge','expected_version']
    when 'posts' then array['scope','limit','cursor','q','active_only']
    when 'create' then array['value','request_key'] when 'edit' then array['post_id','value','expected_version']
    when 'post' then array['post_id'] when 'join' then array['post_id','expected_version']
    when 'requests' then array['post_id'] when 'decide' then array['join_id','status']
    when 'withdraw' then array['post_id'] when 'cancel' then array['post_id'] else null end;
  if allowed is null or args - allowed <> '{}'::jsonb then
    raise exception using errcode='PT422',message='invalid_input'; end if;
  insert into public.core_profiles(id,display_name)
    select id,coalesce(nullif(left(btrim(raw_user_meta_data->>'display_name'),40),''),'新朋友')
    from auth.users where id=actor on conflict(id) do nothing;

  if action='me' then return core_private.profile_view(actor); end if;
  if action='profile' then
    if jsonb_typeof(args->'display_name') is distinct from 'string' or
      jsonb_typeof(args->'bio') is distinct from 'string' or
      jsonb_typeof(args->'badge') is distinct from 'string' or
      jsonb_typeof(args->'expected_version') is distinct from 'number' or
      (args->>'expected_version') !~ '^[1-9][0-9]*$'
    then raise exception using errcode='PT422',message='invalid_profile'; end if;
    update public.core_profiles set display_name=btrim(args->>'display_name'),bio=btrim(args->>'bio'),
      badge=args->>'badge',version=version+1 where id=actor and version=(args->>'expected_version')::integer
      returning * into profile;
    if not found then raise exception using errcode='PT409',message='profile_changed_reload'; end if;
    return core_private.profile_view(actor);
  end if;
  if action='posts' then
    scope:=coalesce(args->>'scope','feed'); term:=btrim(coalesce(args->>'q','')); page_size:=coalesce((args->>'limit')::integer,30);
    if scope not in ('feed','hosted','participating') or page_size not between 1 and 50 or char_length(term)>100 then
      raise exception using errcode='PT422',message='invalid_query'; end if;
    if coalesce(args->>'cursor','')<>'' then
      v_payload:=convert_from(decode(args->>'cursor','base64'),'UTF8')::jsonb;
      last_time:=(v_payload->>0)::timestamptz; last_id:=(v_payload->>1)::uuid;
      if last_time is null or last_id is null or jsonb_array_length(v_payload)<>2 then
        raise exception using errcode='PT422',message='invalid_cursor'; end if;
    end if;
    select coalesce(jsonb_agg(core_private.post_view(p.id,actor) order by p.created_at desc,p.id desc),'[]') into result from (
      select p.* from public.core_posts p join public.core_profiles u on u.id=p.author_id
      where (scope='feed' or (scope='hosted' and p.author_id=actor) or (scope='participating' and exists(
        select 1 from public.core_join_requests j where j.post_id=p.id and j.requester_id=actor and j.status in ('pending','accepted'))))
      and (term='' or strpos(lower(p.payload->>'content'),lower(term))>0 or
        strpos(lower((p.payload->'tags')::text),lower(term))>0 or strpos(lower(u.display_name),lower(term))>0)
      and (not coalesce((args->>'active_only')::boolean,false) or (not p.cancelled and p.deadline_at>clock_timestamp()))
      and (last_time is null or (p.created_at,p.id)<(last_time,last_id))
      order by p.created_at desc,p.id desc limit page_size+1
    ) p;
    if jsonb_array_length(result)>page_size then
      v_payload:=result->(page_size-1);
      next_cursor:=replace(encode(convert_to(jsonb_build_array(v_payload->>'created_at',v_payload->>'id')::text,'UTF8'),'base64'),E'\n','');
      result:=result-page_size;
    end if;
    return jsonb_build_object('items',result,'next_cursor',next_cursor);
  end if;
  if action='create' then
    if jsonb_typeof(args->'request_key') is distinct from 'string' or char_length(coalesce(args->>'request_key','')) not between 12 and 100 then
      raise exception using errcode='PT422',message='invalid_request_key'; end if;
    -- Serialize matching create retries by owner, then enforce a unique owner/key pair.
    perform 1 from public.core_profiles where id=actor for update;
    select * into post from public.core_posts where author_id=actor and request_key=args->>'request_key';
    if found then
      if post.original_payload is distinct from args->'value' then
        raise exception using errcode='PT409',message='idempotency_key_payload_conflict'; end if;
      return core_private.post_view(post.id,actor);
    end if;
    v_payload:=core_private.post_input(args->'value');
    insert into public.core_posts(author_id,payload,deadline_at,capacity,request_key,original_payload)
      values(actor,v_payload,(v_payload->>'deadline_at')::timestamptz,(v_payload->>'capacity')::integer,args->>'request_key',args->'value')
      returning * into post;
    return core_private.post_view(post.id,actor);
  end if;
  if action='decide' then
    select * into request from public.core_join_requests where id=(args->>'join_id')::uuid;
    if not found then raise exception using errcode='PT404',message='join_request_not_found'; end if;
    target_post_id:=request.post_id;
  else target_post_id:=(args->>'post_id')::uuid;
  end if;
  -- Every membership / edit operation locks the same parent row before inspecting children.
  select * into post from public.core_posts where id=target_post_id for update;
  if not found then raise exception using errcode='PT404',message='post_not_found'; end if;
  if action='post' then return core_private.post_view(post.id,actor); end if;
  if action in ('edit','requests','decide','cancel') and post.author_id<>actor then
    raise exception using errcode='PT403',message='host_only'; end if;
  if action='requests' then
    select coalesce(jsonb_agg(core_private.join_view(j) order by j.created_at,j.id),'[]') into result
      from public.core_join_requests j where j.post_id=post.id;
    return result;
  end if;
  if action='cancel' and post.cancelled then return core_private.post_view(post.id,actor); end if;
  if action='decide' then
    select * into request from public.core_join_requests where id=request.id;
    if args->>'status' not in ('accepted','rejected') or args->>'status' is null then
      raise exception using errcode='PT422',message='invalid_decision'; end if;
    if request.status=args->>'status' then return core_private.join_view(request); end if;
  end if;
  if action='withdraw' then
    if post.author_id=actor then raise exception using errcode='PT409',message='host_must_cancel_post'; end if;
    select * into request from public.core_join_requests where post_id=post.id and requester_id=actor;
    if not found or request.status in ('cancelled','rejected') then return core_private.post_view(post.id,actor); end if;
  end if;
  if post.cancelled then raise exception using errcode='PT409',message='post_cancelled'; end if;
  if post.deadline_at<=clock_timestamp() then raise exception using errcode='PT409',message='post_deadline_passed'; end if;
  if action in ('edit','join') then
    if jsonb_typeof(args->'expected_version') is distinct from 'number' or
      (args->>'expected_version') !~ '^[1-9][0-9]*$' then
      raise exception using errcode='PT422',message='invalid_version'; end if;
    expected:=(args->>'expected_version')::integer;
    if post.version<>expected then raise exception using errcode='PT409',message='post_changed_reload'; end if;
  end if;
  if action='edit' then
    if exists(select 1 from public.core_join_requests where post_id=post.id and status in ('pending','accepted')) then
      raise exception using errcode='PT409',message='post_has_active_requests'; end if;
    v_payload:=core_private.post_input(args->'value');
    update public.core_posts set payload=v_payload,deadline_at=(v_payload->>'deadline_at')::timestamptz,
      capacity=(v_payload->>'capacity')::integer,version=version+1 where id=post.id returning * into post;
    return core_private.post_view(post.id,actor);
  end if;
  select 1+count(*) into n from public.core_join_requests where post_id=post.id and status='accepted';
  if action='join' then
    if post.author_id=actor then raise exception using errcode='PT409',message='host_already_joined'; end if;
    select * into request from public.core_join_requests where post_id=post.id and requester_id=actor;
    if found and request.status in ('pending','accepted') then return core_private.join_view(request); end if;
    if n>=post.capacity then raise exception using errcode='PT409',message='post_full'; end if;
    insert into public.core_join_requests(post_id,requester_id,status) values(post.id,actor,'pending')
      on conflict(post_id,requester_id) do update set status='pending',created_at=clock_timestamp(),decided_at=null
      returning * into request;
    return core_private.join_view(request);
  elsif action='decide' then
    if request.status<>'pending' then raise exception using errcode='PT409',message='join_request_not_pending'; end if;
    if args->>'status'='accepted' and n>=post.capacity then raise exception using errcode='PT409',message='post_full'; end if;
    update public.core_join_requests set status=args->>'status',decided_at=clock_timestamp() where id=request.id returning * into request;
    return core_private.join_view(request);
  elsif action='withdraw' then
    update public.core_join_requests set status='cancelled',decided_at=clock_timestamp() where id=request.id;
  elsif action='cancel' then
    update public.core_posts set cancelled=true where id=post.id returning * into post;
  end if;
  return core_private.post_view(post.id,actor);
exception when data_exception or check_violation or not_null_violation then
  raise exception using errcode='PT422',message='invalid_input';
end;
$function$;

CREATE OR REPLACE FUNCTION core_private.chat_message_view(message core_chat_messages)
 RETURNS jsonb
 LANGUAGE sql
 STABLE
 SET search_path TO ''
AS $function$
 select (select core_private.chat_message_view_before_photos(message)||jsonb_build_object('image_id',case when not message.moderation_hidden then (select asset_id from public.core_chat_photos where message_id=message.id) end)) || jsonb_build_object('event_kind',message.event_kind);
$function$;

