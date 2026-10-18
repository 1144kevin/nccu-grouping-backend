-- Cloud user blocking. Public RPC signatures stay stable; original implementations stay private.
create table public.core_user_blocks (
 owner_id uuid not null references public.core_profiles(id) on delete cascade,
 blocked_id uuid not null references public.core_profiles(id) on delete cascade,
 created_at timestamptz not null default clock_timestamp(),
 primary key(owner_id,blocked_id), check(owner_id<>blocked_id)
);
create index core_user_blocks_target on public.core_user_blocks(blocked_id,owner_id);
alter table public.core_user_blocks enable row level security;
revoke all on public.core_user_blocks from public,anon,authenticated;
create policy core_user_blocks_rpc_only on public.core_user_blocks for all to anon,authenticated using(false) with check(false);

create function core_private.account_pending(actor uuid) returns boolean
language sql stable security definer set search_path='' as $$ select false $$;
revoke all on function core_private.account_pending(uuid) from public,anon,authenticated;

create function core_private.account_guard(allow_pending boolean default false) returns uuid
language plpgsql security definer set search_path='' as $$
declare actor uuid:=auth.uid();
begin
 perform pg_advisory_xact_lock(hashtextextended('core-account-safety-v1',0));
 perform 1 from auth.users where id=actor and not coalesce(is_anonymous,false) for key share;
 if not found or not exists(select 1 from auth.sessions where user_id=actor
   and id::text=auth.jwt()->>'session_id' and (not_after is null or not_after>clock_timestamp())) then
  raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
 if not allow_pending and core_private.account_pending(actor) then
  raise exception using errcode='PT409',message='account_deletion_pending'; end if;
 return actor;
end $$;
revoke all on function core_private.account_guard(boolean) from public,anon,authenticated;

create function core_private.users_blocked(a uuid,b uuid) returns boolean
language sql stable security definer set search_path='' as $$
 select exists(select 1 from public.core_user_blocks where (owner_id=a and blocked_id=b) or (owner_id=b and blocked_id=a));
$$;
revoke all on function core_private.users_blocked(uuid,uuid) from public,anon,authenticated;

create function core_private.group_send_allowed(actor uuid,post_id uuid) returns boolean
language sql stable security definer set search_path='' as $$
 select not exists(select 1 from (
  select author_id id from public.core_posts where id=post_id
  union select requester_id from public.core_join_requests where core_join_requests.post_id=group_send_allowed.post_id and status='accepted'
 ) m where core_private.users_blocked(actor,m.id));
$$;
revoke all on function core_private.group_send_allowed(uuid,uuid) from public,anon,authenticated;

create function core_private.post_accessible(actor uuid,post_id uuid) returns boolean
language sql stable security definer set search_path='' as $$
 select exists(select 1 from public.core_posts p where p.id=post_id and (
  not core_private.users_blocked(actor,p.author_id) or p.author_id=actor or exists(
   select 1 from public.core_join_requests j where j.post_id=p.id and j.requester_id=actor and j.status='accepted')));
$$;
revoke all on function core_private.post_accessible(uuid,uuid) from public,anon,authenticated;

create function core_private.visible_unread(actor uuid,target uuid) returns integer
language sql stable security definer set search_path='' as $$
 select count(*)::integer from public.core_chat_messages m
 left join public.core_chat_reads r on r.post_id=m.post_id and r.viewer_id=actor
 where m.post_id=target and m.sender_id<>actor and not core_private.users_blocked(actor,m.sender_id)
 and (r.last_read_at is null or (m.created_at,m.id)>(r.last_read_at,r.last_read_id));
$$;
revoke all on function core_private.visible_unread(uuid,uuid) from public,anon,authenticated;

create function core_private.block_users(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=core_private.account_guard(); target uuid; result jsonb;
begin
 if jsonb_typeof(args) is distinct from 'object' or action not in ('list','put','remove')
  or args-(case when action='list' then array[]::text[] else array['user_id'] end)<>'{}' then
  raise exception using errcode='PT422',message='invalid_block_input'; end if;
 if action='list' then
  select coalesce(jsonb_agg(jsonb_build_object('user_id',b.blocked_id,'display_name',p.display_name,'created_at',b.created_at)
   order by b.created_at desc,b.blocked_id),'[]') into result
   from public.core_user_blocks b join public.core_profiles p on p.id=b.blocked_id where b.owner_id=actor;
  return jsonb_build_object('items',result);
 end if;
 target:=(args->>'user_id')::uuid;
 if target is null or target=actor then raise exception using errcode='PT422',message='invalid_block_input'; end if;
 if action='remove' then
  delete from public.core_user_blocks where owner_id=actor and blocked_id=target;
 else
  if not exists(select 1 from public.core_profiles where id=target) then
   raise exception using errcode='PT404',message='public_profile_not_found'; end if;
  if not exists(select 1 from public.core_user_blocks where owner_id=actor and blocked_id=target)
   and (select count(*) from public.core_user_blocks where owner_id=actor)>=500 then
   raise exception using errcode='PT409',message='block_limit_reached'; end if;
  insert into public.core_user_blocks(owner_id,blocked_id) values(actor,target) on conflict do nothing;
  with cancelled as (
   update public.core_join_requests j set status='cancelled',decided_at=clock_timestamp()
   from public.core_posts p where p.id=j.post_id and j.status='pending'
    and ((p.author_id=actor and j.requester_id=target) or (p.author_id=target and j.requester_id=actor))
   returning j.post_id
  ) update public.core_posts set version=version+1 where id in (select post_id from cancelled);
  -- Do not revive old invitations after unblocking; formed records remain history.
  update public.core_stealth_proposals set payload=payload||jsonb_build_object('expires_at',clock_timestamp()),
   terms_acceptances='{}' where actor=any(owners) and target=any(owners) and formed_post_id is null;
 end if;
 return jsonb_build_object('user_id',target,'blocked',action='put');
exception when invalid_text_representation then raise exception using errcode='PT422',message='invalid_block_input';
end $$;
revoke all on function core_private.block_users(text,jsonb) from public,anon,authenticated;
grant execute on function core_private.block_users(text,jsonb) to authenticated;
create function public.core_user_blocks(action text,args jsonb default '{}') returns jsonb
language sql security invoker set search_path='' as $$ select core_private.block_users(action,args) $$;
revoke all on function public.core_user_blocks(text,jsonb) from public,anon,authenticated;
grant execute on function public.core_user_blocks(text,jsonb) to authenticated;

create function core_private.safety_call(kind text,action text,args jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=core_private.account_guard(kind='dispatch' and action='me'); result jsonb;
 target uuid; item jsonb; items jsonb:='[]'; peers uuid[]; author uuid; last_message jsonb; total integer:=0;
begin
 if kind='dispatch' and action in ('post','join') then
  target:=(args->>'post_id')::uuid;
  if not core_private.post_accessible(actor,target) then raise exception using errcode='PT404',message='post_not_found'; end if;
  select author_id into author from public.core_posts where id=target;
  if action='join' and core_private.users_blocked(actor,author) then
   raise exception using errcode='PT403',message='user_interaction_blocked'; end if;
 elsif kind='dispatch' and action='decide' then
  select requester_id into author from public.core_join_requests where id=(args->>'join_id')::uuid;
  if core_private.users_blocked(actor,author) then raise exception using errcode='PT403',message='user_interaction_blocked'; end if;
 elsif kind='public_user' and core_private.users_blocked(actor,(args->>'user_id')::uuid) then
  raise exception using errcode='PT404',message='public_profile_not_found';
 elsif kind='chat' and action='send' and not core_private.group_send_allowed(actor,(args->>'post_id')::uuid) then
  raise exception using errcode='PT403',message='blocked_group_interaction';
 elsif kind in ('stealth_cloud','stealth_formation') and args ? 'proposal_id' then
  select owners into peers from public.core_stealth_proposals where id=(args->>'proposal_id')::uuid;
  if exists(select 1 from unnest(peers) a cross join unnest(peers) b where core_private.users_blocked(a,b)) then
   raise exception using errcode='PT409',message='proposal_unavailable'; end if;
 end if;
 result:=case kind
  when 'dispatch' then core_private.dispatch(action,args)
  when 'chat' then core_private.chat(action,args)
  when 'chat_activity' then core_private.chat_activity(action,args)
  when 'public_user' then core_private.public_user(action,args)
  when 'order_history' then core_private.order_history(args)
  when 'order_activity' then core_private.order_activity(args)
  when 'engagement' then core_private.engagement(action,args)
  when 'search' then core_private.search(args)
  when 'recommendation_feed' then core_private.recommendation_feed(args)
  when 'saved_posts' then core_private.saved_posts(action,args)
  when 'media' then core_private.media(action,args)
  when 'stealth_cloud' then core_private.stealth_cloud(action,args)
  when 'stealth_formation' then core_private.stealth_formation(action,args)
  when 'clear_demo' then core_private.clear_demo()
  when 'server_clock' then core_private.server_clock()
  else null end;
 if result is null then raise exception using errcode='PT422',message='invalid_safety_operation'; end if;
 if kind='dispatch' and action='me' then return result||jsonb_build_object('deletion_pending',core_private.account_pending(actor)); end if;
 if kind='chat' and action='messages' then
  select coalesce(jsonb_agg(v),'[]') into items from jsonb_array_elements(result->'messages') v
   where not core_private.users_blocked(actor,(v->>'sender_id')::uuid);
  return result||jsonb_build_object('messages',items,'can_send',coalesce((result->>'can_send')::boolean,false)
   and core_private.group_send_allowed(actor,(args->>'post_id')::uuid));
 elsif kind='chat_activity' and action='status' then
  for item in select * from jsonb_array_elements(result->'groups') loop
   target:=(item->>'post_id')::uuid;
   item:=item||jsonb_build_object('unread_count',core_private.visible_unread(actor,target));
   total:=total+(item->>'unread_count')::integer; items:=items||jsonb_build_array(item);
  end loop;
  return result||jsonb_build_object('groups',items,'total_unread',total);
 end if;
 if result ? 'items' then
  for item in select * from jsonb_array_elements(result->'items') loop
   if (kind='dispatch' and action='posts' and coalesce(args->>'scope','feed')='feed') or kind='recommendation_feed' then
    -- Recommendation entries wrap the post; normal feed entries are the post itself.
    author:=coalesce(item->'post'->>'author_id',item->>'author_id')::uuid;
    if core_private.users_blocked(actor,author) then continue; end if;
   elsif kind='search' then
    if item->>'kind'='user' and core_private.users_blocked(actor,(item->>'id')::uuid) then continue; end if;
    if item->>'kind'='message' then
     select sender_id into author from public.core_chat_messages where id=(item->>'id')::uuid;
     if core_private.users_blocked(actor,author) then continue; end if;
    end if;
   elsif kind='chat' and action='list' then
    target:=(item->'post'->>'id')::uuid;
    select core_private.chat_message_view(m) into last_message from public.core_chat_messages m
     where m.post_id=target and not core_private.users_blocked(actor,m.sender_id) order by m.created_at desc,m.id desc limit 1;
    item:=item||jsonb_build_object('last_message',last_message);
   elsif kind='stealth_cloud' and action='matches' then
    select owners into peers from public.core_stealth_proposals where id=(item->>'id')::uuid;
    if exists(select 1 from unnest(peers) a cross join unnest(peers) b where core_private.users_blocked(a,b)) then continue; end if;
   end if;
   items:=items||jsonb_build_array(item);
  end loop;
  return result||jsonb_build_object('items',items);
 end if;
 return result;
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range then
 raise exception using errcode='PT422',message='invalid_safety_input';
end $$;
revoke all on function core_private.safety_call(text,text,jsonb) from public,anon,authenticated;
grant execute on function core_private.safety_call(text,text,jsonb) to authenticated;

-- Static allowlist only. Revoke direct calls to the old private implementations.
do $wrap$
declare spec record; signature text; action_arg text; args_arg text;
begin
 for spec in select * from (values
  ('core_dispatch','dispatch',2),('core_chat','chat',2),('core_chat_activity','chat_activity',2),
  ('core_public_user','public_user',2),('core_engagement','engagement',2),('core_saved_posts','saved_posts',2),
  ('core_media','media',2),('core_stealth','stealth_cloud',2),('core_stealth_formation','stealth_formation',2),
  ('core_search','search',1),('core_recommendation_feed','recommendation_feed',1),
  ('core_order_history','order_history',1),('core_order_activity','order_activity',1),
  ('core_clear_demo','clear_demo',0),('core_server_clock','server_clock',0)
 ) as s(public_name,private_name,arity) loop
  signature:=case spec.arity when 2 then 'action text,args jsonb default ''{}''' when 1 then 'args jsonb default ''{}''' else '' end;
  action_arg:=case when spec.arity=2 then 'action' else '''''' end;
  args_arg:=case when spec.arity>0 then 'args' else '''{}''::jsonb' end;
  execute format('create or replace function public.%I(%s) returns jsonb language sql security invoker set search_path='''' as $fn$ select core_private.safety_call(%L,%s,%s) $fn$',spec.public_name,signature,spec.private_name,action_arg,args_arg);
  execute format('revoke all on function core_private.%I(%s) from public,anon,authenticated',spec.private_name,
   case spec.arity when 2 then 'text,jsonb' when 1 then 'jsonb' else '' end);
 end loop;
end $wrap$;

do $$ begin
 execute replace(pg_get_functiondef('core_private.stealth_candidate(uuid[],timestamptz)'::regprocedure),
  'core_private.stealth_candidate(', 'core_private.stealth_candidate_before_user_blocks(');
end $$;
revoke all on function core_private.stealth_candidate_before_user_blocks(uuid[],timestamptz) from public,anon,authenticated;
create or replace function core_private.stealth_candidate(ids uuid[],ts timestamptz) returns jsonb
language plpgsql security definer set search_path='' as $$
declare owners uuid[];
begin
 select array_agg(owner_id) into owners from public.core_stealth_signals where id=any(ids);
 if exists(select 1 from unnest(owners) a cross join unnest(owners) b where core_private.users_blocked(a,b)) then return null; end if;
 return core_private.stealth_candidate_before_user_blocks(ids,ts);
end $$;
revoke all on function core_private.stealth_candidate(uuid[],timestamptz) from public,anon,authenticated;

do $$ begin
 execute replace(pg_get_functiondef('core_private.media_access(text,text)'::regprocedure),
  'core_private.media_access(', 'core_private.media_access_before_user_safety(');
end $$;
revoke all on function core_private.media_access_before_user_safety(text,text) from public,anon,authenticated;
create or replace function core_private.media_access(operation text,object_name text) returns boolean
language plpgsql security definer set search_path='' as $$
declare actor uuid:=core_private.media_actor(); asset public.core_media_assets;
begin
 if not core_private.media_access_before_user_safety(operation,object_name) then return false; end if;
 if operation='select' then
  select * into asset from public.core_media_assets a where object_name=a.owner_id::text||'/'||a.id::text||'.jpg';
  if core_private.users_blocked(actor,asset.owner_id) and not exists(select 1 from public.core_post_images i
    where i.asset_id=asset.id and core_private.post_accessible(actor,i.post_id)) then return false; end if;
 end if;
 return true;
end $$;
revoke all on function core_private.media_access(text,text) from public,anon,authenticated;
grant execute on function core_private.media_access(text,text) to authenticated;
