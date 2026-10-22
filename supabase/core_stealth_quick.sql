-- Additive: preliminary private coordination is NOT a formal order or payment consent.
create table public.core_stealth_rooms (
 proposal_id uuid primary key references public.core_stealth_proposals(id) on delete cascade,
 created_at timestamptz not null default clock_timestamp()
);
create table public.core_stealth_room_messages (
 id uuid primary key default gen_random_uuid(),
 proposal_id uuid not null references public.core_stealth_rooms(proposal_id) on delete cascade,
 sender_id uuid not null references public.core_profiles(id) on delete cascade,
 content text not null check(char_length(btrim(content)) between 1 and 2000),
 request_key uuid not null,
 created_at timestamptz not null default clock_timestamp(),
 unique(sender_id,request_key)
);
create index core_stealth_room_messages_recent on public.core_stealth_room_messages(proposal_id,created_at desc,id);
alter table public.core_stealth_rooms enable row level security;
alter table public.core_stealth_room_messages enable row level security;
revoke all on public.core_stealth_rooms,public.core_stealth_room_messages from public,anon,authenticated;
create policy core_stealth_rooms_rpc_only on public.core_stealth_rooms for all to anon,authenticated using(false) with check(false);
create policy core_stealth_room_messages_rpc_only on public.core_stealth_room_messages for all to anon,authenticated using(false) with check(false);

create function core_private.stealth_quick(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=core_private.account_guard(); p public.core_stealth_proposals;
 s public.core_stealth_signals; m public.core_stealth_room_messages; v jsonb; items jsonb; live boolean; allowed text[];
begin
 allowed:=case action when 'lookup' then array['request_key'] when 'state' then array['proposal_id']
  when 'send' then array['proposal_id','request_key','content'] else null end;
 if allowed is null or jsonb_typeof(args) is distinct from 'object' or args-allowed<>'{}' or not args ?& allowed then
  raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
 if action='lookup' then
  select * into s from public.core_stealth_signals where owner_id=actor and request_key=(args->>'request_key')::uuid;
  if not found then return 'null'::jsonb; end if;
  return s.payload||jsonb_build_object('id',s.id,'owner_id',actor,'status',s.status,'version',s.version,'created_at',s.created_at);
 end if;
 select * into p from public.core_stealth_proposals where id=(args->>'proposal_id')::uuid and actor=any(owners) for update;
 if not found or exists(select 1 from unnest(p.owners) a cross join unnest(p.owners) b where core_private.users_blocked(a,b))
  or exists(select 1 from unnest(p.owners) u where core_private.account_pending(u)) then
  raise exception using errcode='PT404',message='stealth_not_found'; end if;
 v:=core_private.stealth_view(p,actor);
 if coalesce((v->>'is_demo')::boolean,false) then raise exception using errcode='PT409',message='demo_cannot_form'; end if;
 live:=v->>'status' in ('ready','formed');
 if live then insert into public.core_stealth_rooms(proposal_id) values(p.id) on conflict do nothing; end if;
 if not exists(select 1 from public.core_stealth_rooms where proposal_id=p.id) then
  if action='send' then raise exception using errcode='PT409',message='coordination_waiting_for_all'; end if;
  return jsonb_build_object('opened',false,'can_send',false,'items','[]'::jsonb);
 end if;
 if action='send' then
  if not live then raise exception using errcode='PT409',message='proposal_unavailable'; end if;
  if jsonb_typeof(args->'content')<>'string' or char_length(btrim(args->>'content')) not between 1 and 2000 then
   raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
  select * into m from public.core_stealth_room_messages where sender_id=actor and request_key=(args->>'request_key')::uuid;
  if found then
   if m.proposal_id<>p.id or m.content<>btrim(args->>'content') then raise exception using errcode='PT409',message='idempotency_payload_conflict'; end if;
  else
   if (select count(*) from public.core_stealth_room_messages where sender_id=actor and created_at>clock_timestamp()-interval '1 minute')>=30 then
    raise exception using errcode='PT429',message='chat_rate_limited'; end if;
   insert into public.core_stealth_room_messages(proposal_id,sender_id,content,request_key)
    values(p.id,actor,btrim(args->>'content'),(args->>'request_key')::uuid) returning * into m;
  end if;
  return jsonb_build_object('id',m.id,'content',m.content,'created_at',m.created_at,'member_number',array_position(p.owners,m.sender_id),'is_me',true);
 end if;
 select coalesce(jsonb_agg(jsonb_build_object('id',x.id,'content',x.content,'created_at',x.created_at,
  'member_number',array_position(p.owners,x.sender_id),'is_me',x.sender_id=actor) order by x.created_at,x.id),'[]'::jsonb) into items
  from (select * from public.core_stealth_room_messages where proposal_id=p.id order by created_at desc,id desc limit 100) x;
 return jsonb_build_object('opened',true,'can_send',live,'items',items);
exception when invalid_text_representation or not_null_violation or check_violation then
 raise exception using errcode='PT422',message='invalid_stealth_input';
end $$;
revoke all on function core_private.stealth_quick(text,jsonb) from public,anon,authenticated;
grant execute on function core_private.stealth_quick(text,jsonb) to authenticated;
create function public.core_stealth_quick(action text,args jsonb default '{}') returns jsonb
language sql security invoker set search_path='' as $$ select core_private.stealth_quick(action,args) $$;
revoke all on function public.core_stealth_quick(text,jsonb) from public,anon,authenticated;
grant execute on function public.core_stealth_quick(text,jsonb) to authenticated;

-- Keep opened coordination rooms discoverable even when the invitation becomes read-only.
do $$ begin
 execute replace(pg_get_functiondef('core_private.stealth_view(public.core_stealth_proposals,uuid)'::regprocedure),
  'core_private.stealth_view(', 'core_private.stealth_view_before_quick(');
end $$;
revoke all on function core_private.stealth_view_before_quick(public.core_stealth_proposals,uuid) from public,anon,authenticated;
create or replace function core_private.stealth_view(p public.core_stealth_proposals,actor uuid) returns jsonb
language plpgsql stable set search_path='' as $$
declare v jsonb;
begin
 v:=core_private.stealth_view_before_quick(p,actor);
 if v is null then return null; end if;
 return v||jsonb_build_object('coordination_opened',exists(select 1 from public.core_stealth_rooms where proposal_id=p.id));
end $$;
revoke all on function core_private.stealth_view(public.core_stealth_proposals,uuid) from public,anon,authenticated;

-- Preserve all current privacy, blocking, test-scope and matching constraints.
do $$ begin
 execute replace(pg_get_functiondef('core_private.stealth_candidate(uuid[],timestamptz)'::regprocedure),
  'core_private.stealth_candidate(', 'core_private.stealth_candidate_before_quick_brand(');
end $$;
revoke all on function core_private.stealth_candidate_before_quick_brand(uuid[],timestamptz) from public,anon,authenticated;
create or replace function core_private.stealth_candidate(ids uuid[],ts timestamptz) returns jsonb
language plpgsql set search_path='' as $$
declare brand text;
begin
 for brand in select distinct t.value from public.core_stealth_signals s
  cross join lateral jsonb_array_elements_text(s.payload->'tags') t(value)
  where s.id=any(ids) and t.value like '品牌:%' loop
  if exists(select 1 from public.core_stealth_signals s where s.id=any(ids) and not s.payload->'tags' ? brand) then return null; end if;
 end loop;
 return core_private.stealth_candidate_before_quick_brand(ids,ts);
end $$;
revoke all on function core_private.stealth_candidate(uuid[],timestamptz) from public,anon,authenticated;
