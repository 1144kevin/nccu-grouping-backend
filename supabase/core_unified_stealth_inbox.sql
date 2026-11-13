-- Accepted stealth conversations share the paginated inbox and owner-scoped preferences.
alter table public.core_thread_preferences drop constraint core_thread_preferences_kind_check;
alter table public.core_thread_preferences add constraint core_thread_preferences_kind_check
 check(kind in ('group','direct','stealth'));

alter function core_private.social_thread(text,uuid,uuid) rename to social_thread_before_unified;
revoke all on function core_private.social_thread_before_unified(text,uuid,uuid) from public,anon,authenticated;

create function core_private.social_thread(k text,target uuid,actor uuid) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare proposal public.core_stealth_proposals; pref public.core_thread_preferences;
 v jsonb; msg jsonb; last_at timestamptz; unread integer; badge text;
begin
 if k<>'stealth' then return core_private.social_thread_before_unified(k,target,actor); end if;
 select * into proposal from public.core_stealth_proposals where id=target and actor=any(owners);
 if not found or exists(select 1 from unnest(proposal.owners) a cross join unnest(proposal.owners) b where core_private.users_blocked(a,b))
  or exists(select 1 from unnest(proposal.owners) u where core_private.account_pending(u)) then return null; end if;
 v:=core_private.stealth_view(proposal,actor);
 if v is null or v->>'my_decision'<>'accepted'
  or (v->>'status'='unavailable' and not coalesce((v->>'coordination_opened')::boolean,false)) then return null; end if;
 select * into pref from public.core_thread_preferences where owner_id=actor and kind=k and thread_id=target;
 -- The coordination room is anonymous; do not expose other members' identities here.
 select jsonb_build_object('id',m.id,'sender_id',null,'sender_name',case when m.sender_id=actor then '你' else '夥伴 '||array_position(proposal.owners,m.sender_id) end,
  'sender_badge','🍔','content',m.content,'created_at',m.created_at),m.created_at into msg,last_at
  from public.core_stealth_room_messages m where proposal_id=target order by created_at desc,id desc limit 1;
 last_at:=coalesce(last_at,proposal.created_at);
 select count(*)::integer into unread from public.core_stealth_room_messages m where proposal_id=target and sender_id<>actor and (pref.read_at is null or created_at>pref.read_at);
 badge:=case v->>'category' when 'pizza' then '🍕' when 'drink' then '🧋' when 'bakery' then '🥐' when 'snack' then '🍟' else '🍱' end;
 return jsonb_build_object('id',target,'kind','stealth','post',null,'title',v->>'my_signal_title',
  'preview',case when coalesce((v->>'coordination_opened')::boolean,false) or v->>'status' in ('ready','formed') then '進入聊天室協調' else '已接受 · 等待夥伴回覆' end,
  'peer_id',actor,'peer_name','偷揪夥伴','peer_badge',badge,'peer_level',1,'last_message',msg,'updated_at',last_at,
  'unread_count',case when coalesce(pref.muted,false) then 0 else unread end,'pinned',coalesce(pref.pinned,false),
  'inbox',coalesce(pref.inbox,'general'),'muted',coalesce(pref.muted,false),
  'deleted',pref.hidden_at is not null and last_at<=pref.hidden_at);
end $$;
revoke all on function core_private.social_thread(text,uuid,uuid) from public,anon,authenticated;

-- Reading the existing coordination room also updates only this member's inbox read marker.
alter function core_private.stealth_quick(text,jsonb) rename to stealth_quick_before_inbox;
revoke all on function core_private.stealth_quick_before_inbox(text,jsonb) from public,anon,authenticated;
create function core_private.stealth_quick(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare value jsonb; actor uuid; latest timestamptz;
begin
 value:=core_private.stealth_quick_before_inbox(action,args);
 if action='state' and coalesce((value->>'opened')::boolean,false) then
  actor:=core_private.account_guard();
  select max((m->>'created_at')::timestamptz) into latest from jsonb_array_elements(value->'items') m;
  if latest is not null then
   insert into public.core_thread_preferences(owner_id,kind,thread_id,read_at)
    values(actor,'stealth',(args->>'proposal_id')::uuid,latest)
    on conflict(owner_id,kind,thread_id) do update set read_at=greatest(core_thread_preferences.read_at,excluded.read_at);
  end if;
 end if;
 return value;
end $$;
revoke all on function core_private.stealth_quick(text,jsonb) from public,anon,authenticated;
grant execute on function core_private.stealth_quick(text,jsonb) to authenticated;

create or replace function core_private.social(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
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
   if p.cancelled or p.deadline_at>clock_timestamp() or p.demo_batch is not null or not exists(select 1 from public.core_join_requests where post_id=target and status='accepted') then
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
   'can_confirm',is_member and not p.cancelled and p.demo_batch is null and p.deadline_at<=clock_timestamp() and (value->>'joined')::integer>1);
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
   select 'group' kind,pp.id from public.core_posts pp where k in ('all','primary','group') and not coalesce(pp.payload->'tags','[]'::jsonb) ? '偷揪成團' and (pp.author_id=actor or exists(select 1 from public.core_join_requests j where j.post_id=pp.id and j.requester_id=actor and j.status='accepted'))
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
end $$;
