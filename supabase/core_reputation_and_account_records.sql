-- Public reputation derives only from completed, unanimously confirmed orders.
create function core_private.reputation_level(target uuid) returns integer
language sql stable security definer set search_path='' as $$
 select greatest(1,least(5,(greatest(1,count(*)::integer*10)+19)/20))
 from public.core_handoff_receipts r where r.member_id=target and core_private.handoff_complete(r.post_id);
$$;
revoke all on function core_private.reputation_level(uuid) from public,anon,authenticated;

create or replace function core_private.post_view(target_id uuid,actor uuid) returns jsonb
language sql stable set search_path = '' as $$
  select post.payload || jsonb_build_object(
    'id',post.id,'author_id',post.author_id,'author_name',p.display_name,'author_badge',p.badge,'author_level',core_private.reputation_level(p.id),
    'is_demo',post.demo_batch is not null,
    'image_id',(select pi.asset_id from public.core_post_images pi where pi.post_id=post.id),
    'created_at',post.created_at,'version',post.version,'joined',1+counts.accepted,
    'status',case when post.cancelled then 'cancelled' when post.deadline_at<=clock_timestamp() then 'expired'
      when 1+counts.accepted>=post.capacity then 'full' else 'open' end,
    'my_status',case when actor=post.author_id then 'host' else coalesce(j.status,'none') end,
    'can_edit',actor=post.author_id and not post.cancelled and post.deadline_at>clock_timestamp() and counts.active=0)
  from public.core_posts post join public.core_profiles p on p.id=post.author_id
  cross join lateral(select count(*) filter(where status='accepted') as accepted,
    count(*) filter(where status in ('pending','accepted')) as active from public.core_join_requests where post_id=post.id) counts
  left join public.core_join_requests j on j.post_id=post.id and j.requester_id=actor where post.id=target_id;
$$;

-- Public profile fields and public posts, scoped by immutable user UUID.
create or replace function core_private.public_user(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '8s' as $$
declare actor uuid:=auth.uid(); target uuid; page_size integer; value jsonb; result jsonb;
  last_time timestamptz; last_id uuid; next_cursor text; allowed text[];
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false)) or
    not exists(select 1 from auth.sessions where id=(auth.jwt()->>'session_id')::uuid and user_id=actor
      and (not_after is null or not_after>clock_timestamp()))
  then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  allowed:=case action when 'profile' then array['user_id'] when 'posts' then array['user_id','limit','cursor'] else null end;
  if allowed is null or jsonb_typeof(args) is distinct from 'object' or args-allowed<>'{}'::jsonb then
    raise exception using errcode='PT422',message='invalid_query'; end if;
  target:=(args->>'user_id')::uuid;
  if not exists(select 1 from public.core_profiles where id=target) then
    raise exception using errcode='PT404',message='public_profile_not_found'; end if;
  if action='profile' then
    select jsonb_build_object('id',u.id,'display_name',u.display_name,'bio',u.bio,'badge',u.badge,'created_at',u.created_at,'level',core_private.reputation_level(u.id),
      'active_posts',(select count(*) from public.core_posts p where p.author_id=u.id and not p.cancelled and p.deadline_at>clock_timestamp()))
    into result from public.core_profiles u where u.id=target;
    return result;
  end if;
  page_size:=coalesce((args->>'limit')::integer,30);
  if page_size not between 1 and 50 then raise exception using errcode='PT422',message='invalid_query'; end if;
  if coalesce(args->>'cursor','')<>'' then
    value:=convert_from(decode(args->>'cursor','base64'),'UTF8')::jsonb;
    if jsonb_typeof(value) is distinct from 'array' or jsonb_array_length(value)<>2 then
      raise exception using errcode='PT422',message='invalid_cursor'; end if;
    last_time:=(value->>0)::timestamptz; last_id:=(value->>1)::uuid;
    if last_time is null or last_id is null then raise exception using errcode='PT422',message='invalid_cursor'; end if;
  end if;
  select coalesce(jsonb_agg(core_private.post_view(p.id,actor) order by p.created_at desc,p.id desc),'[]') into result from (
    select p.* from public.core_posts p where p.author_id=target
      and (last_time is null or (p.created_at,p.id)<(last_time,last_id))
    order by p.created_at desc,p.id desc limit page_size+1
  ) p;
  if jsonb_array_length(result)>page_size then
    value:=result->(page_size-1);
    next_cursor:=replace(encode(convert_to(jsonb_build_array(value->>'created_at',value->>'id')::text,'UTF8'),'base64'),E'\n','');
    result:=result-page_size;
  end if;
  return jsonb_build_object('items',result,'next_cursor',next_cursor);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range
  or character_not_in_repertoire or datetime_field_overflow or invalid_datetime_format then
  raise exception using errcode='PT422',message='invalid_query';
end;
$$;
-- Independent read-only history endpoint. Existing dispatcher is not replaced.
create or replace function core_private.order_history(args jsonb) returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '8s' as $$
declare actor uuid := auth.uid(); result jsonb; value jsonb; page_size integer;
  term text; last_time timestamptz; last_id uuid; next_cursor text;
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false)) or
    not exists(select 1 from auth.sessions where id=(auth.jwt()->>'session_id')::uuid and user_id=actor
      and (not_after is null or not_after>clock_timestamp()))
  then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  if jsonb_typeof(args) is distinct from 'object' or args-array['limit','cursor','q','active_only']<>'{}'::jsonb then
    raise exception using errcode='PT422',message='invalid_query'; end if;
  page_size:=coalesce((args->>'limit')::integer,30); term:=btrim(coalesce(args->>'q',''));
  if page_size not between 1 and 50 or char_length(term)>100 then
    raise exception using errcode='PT422',message='invalid_query'; end if;
  if coalesce(args->>'cursor','')<>'' then
    value:=convert_from(decode(args->>'cursor','base64'),'UTF8')::jsonb;
    if jsonb_typeof(value) is distinct from 'array' or jsonb_array_length(value)<>2 then
      raise exception using errcode='PT422',message='invalid_cursor'; end if;
    last_time:=(value->>0)::timestamptz; last_id:=(value->>1)::uuid;
    if last_time is null or last_id is null then
      raise exception using errcode='PT422',message='invalid_cursor'; end if;
  end if;
  select coalesce(jsonb_agg(core_private.post_view(p.id,actor) order by p.created_at desc,p.id desc),'[]') into result
  from (
    select p.* from public.core_posts p join public.core_profiles u on u.id=p.author_id
    where (p.author_id=actor or exists (
      select 1 from public.core_join_requests j where j.post_id=p.id and j.requester_id=actor))
    and (term='' or strpos(lower(p.payload->>'content'),lower(term))>0 or
      strpos(lower((p.payload->'tags')::text),lower(term))>0 or strpos(lower(u.display_name),lower(term))>0)
    and (not coalesce((args->>'active_only')::boolean,false) or (not p.cancelled and p.deadline_at>clock_timestamp()))
    and (last_time is null or (p.created_at,p.id)<(last_time,last_id))
    order by p.created_at desc,p.id desc limit page_size+1
  ) p;
  if jsonb_array_length(result)>page_size then
    value:=result->(page_size-1);
    next_cursor:=replace(encode(convert_to(jsonb_build_array(value->>'created_at',value->>'id')::text,'UTF8'),'base64'),E'\n','');
    result:=result-page_size;
  end if;
  return jsonb_build_object('items',result,'next_cursor',next_cursor);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range
  or character_not_in_repertoire or datetime_field_overflow or invalid_datetime_format then
  raise exception using errcode='PT422',message='invalid_query';
end;
$$;
create or replace function core_private.social_message(m public.core_direct_messages) returns jsonb
language sql stable security definer set search_path='' as $$
 select jsonb_build_object('id',m.id,'thread_id',m.thread_id,'sender_id',m.sender_id,
 'sender_name',p.display_name,'sender_badge',p.badge,'sender_level',core_private.reputation_level(p.id),'content',m.content,'created_at',m.created_at)
 from public.core_profiles p where p.id=m.sender_id;
$$;
revoke all on function core_private.social_message(public.core_direct_messages) from public,anon,authenticated;

create or replace function core_private.social_thread(k text,target uuid,actor uuid) returns jsonb
language plpgsql stable security definer set search_path='' as $$
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
   where m.post_id=target and not m.moderation_hidden and not core_private.users_blocked(actor,m.sender_id)
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
end $$;
revoke all on function core_private.social_thread(text,uuid,uuid) from public,anon,authenticated;


create or replace function core_private.chat_message_view(message public.core_chat_messages) returns jsonb
language sql stable set search_path = '' as $$
  select jsonb_build_object('id',message.id,'post_id',message.post_id,'sender_id',message.sender_id,
    'sender_name',p.display_name,'sender_badge',p.badge,'sender_level',core_private.reputation_level(p.id),'content',case when message.moderation_hidden then '此訊息已由管理者隱藏' else message.content end,'created_at',message.created_at)
  from public.core_profiles p where p.id=message.sender_id;
$$;
