-- Additive private appeals. No automatic reversal or operator grants.
create table public.core_appeals (
  id uuid primary key default gen_random_uuid(),
  report_id uuid not null references public.core_reports(id) on delete cascade,
  owner_id uuid not null references auth.users(id) on delete cascade,
  detail text not null check(char_length(btrim(detail)) between 10 and 1000),
  request_key uuid not null unique,
  status text not null default 'received' check(status in ('received','resolved','dismissed')),
  version integer not null default 1,
  response text not null default '',
  created_at timestamptz not null default clock_timestamp(),
  unique(report_id,owner_id)
);
create table public.core_appeal_events (
  request_key uuid primary key,
  appeal_id uuid not null references public.core_appeals(id) on delete cascade,
  actor_id uuid references auth.users(id) on delete set null,
  request jsonb not null,
  result jsonb not null,
  created_at timestamptz not null default clock_timestamp()
);
alter table public.core_appeals enable row level security;
alter table public.core_appeal_events enable row level security;
revoke all on public.core_appeals,public.core_appeal_events from public,anon,authenticated;

create function public.core_appeals(action text,args jsonb default '{}') returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=core_private.account_guard(); r public.core_reports; a public.core_appeals;
  event_record public.core_appeal_events; result jsonb;
begin
  if jsonb_typeof(args) is distinct from 'object' then
    raise exception using errcode='PT422',message='invalid_appeal_input'; end if;
  if action='mine' and args='{}' then
    select coalesce(jsonb_agg(to_jsonb(q) order by created_at desc),'[]') into result
      from(select id,report_id,detail,status,version,response,created_at from public.core_appeals
        where owner_id=actor order by created_at desc,id desc limit 50) q;
    return jsonb_build_object('items',result);
  end if;
  if action='decisions' and args='{}' then
    -- Affected authors see disposition IDs only, never reporter identity or private notes.
    select coalesce(jsonb_agg(to_jsonb(q) order by created_at desc),'[]') into result from (
      select distinct rep.id report_id,rep.created_at,rep.payload->>'kind' kind,rep.payload->>'target_id' target_id
      from public.core_reports rep join public.core_moderation_events e on e.report_id=rep.id
      where (e.request->>'action'='cancel_post' and exists(select 1 from public.core_posts p
        where p.id::text=rep.payload->>'target_id' and p.author_id=actor))
      or (e.request->>'action'='hide_message' and exists(select 1 from public.core_chat_messages m
        where m.id::text=rep.payload->>'target_id' and m.sender_id=actor))
      order by rep.created_at desc limit 50) q;
    return jsonb_build_object('items',result);
  end if;
  if action='create' then
    if args-array['report_id','detail','request_key']<>'{}'
      or jsonb_typeof(args->'detail') is distinct from 'string'
      or char_length(btrim(args->>'detail')) not between 10 and 1000
      or args->>'request_key' is null or args->>'report_id' is null then
      raise exception using errcode='PT422',message='invalid_appeal_input'; end if;
    perform pg_advisory_xact_lock(hashtextextended(args->>'request_key',0));
    select * into r from public.core_reports where id=(args->>'report_id')::uuid for update;
    if not found or not (r.owner_id=actor or exists(select 1 from public.core_moderation_events e
      where e.report_id=r.id and (
        (e.request->>'action'='cancel_post' and exists(select 1 from public.core_posts p where p.id::text=r.payload->>'target_id' and p.author_id=actor))
        or (e.request->>'action'='hide_message' and exists(select 1 from public.core_chat_messages m where m.id::text=r.payload->>'target_id' and m.sender_id=actor))))) then
      raise exception using errcode='PT404',message='appeal_target_not_found'; end if;
    select * into a from public.core_appeals where request_key=(args->>'request_key')::uuid;
    if found then
      if a.owner_id<>actor or a.report_id<>r.id or a.detail<>btrim(args->>'detail') then
        raise exception using errcode='PT409',message='appeal_key_conflict'; end if;
      return jsonb_build_object('id',a.id,'status',a.status,'version',a.version);
    end if;
    if r.status not in ('resolved','dismissed') then
      raise exception using errcode='PT409',message='appeal_requires_closed_report'; end if;
    if exists(select 1 from public.core_appeals where report_id=r.id and owner_id=actor) then
      raise exception using errcode='PT409',message='appeal_already_submitted'; end if;
    insert into public.core_appeals(report_id,owner_id,detail,request_key)
      values(r.id,actor,btrim(args->>'detail'),(args->>'request_key')::uuid) returning * into a;
    return jsonb_build_object('id',a.id,'status',a.status,'version',a.version);
  end if;
  if not exists(select 1 from public.core_moderators where user_id=actor and enabled and expires_at>clock_timestamp()) then
    raise exception using errcode='PT403',message='moderator_required'; end if;
  if action='list' and args='{}' then
    select coalesce(jsonb_agg(to_jsonb(q) order by created_at desc),'[]') into result from
      (select * from public.core_appeals order by created_at desc,id desc limit 50) q;
    return jsonb_build_object('items',result);
  end if;
  if action<>'review' or args-array['appeal_id','expected_version','status','response','request_key']<>'{}'
    or not coalesce(args->>'status' in ('resolved','dismissed'),false)
    or jsonb_typeof(args->'response') is distinct from 'string'
    or char_length(btrim(args->>'response')) not between 5 and 300
    or not coalesce(args->>'expected_version' ~ '^[1-9][0-9]{0,8}$',false)
    or args->>'request_key' is null or args->>'appeal_id' is null then
    raise exception using errcode='PT422',message='invalid_appeal_input'; end if;
  perform pg_advisory_xact_lock(hashtextextended(args->>'request_key',0));
  select * into a from public.core_appeals where id=(args->>'appeal_id')::uuid for update;
  if not found then raise exception using errcode='PT404',message='appeal_target_not_found'; end if;
  select * into event_record from public.core_appeal_events where request_key=(args->>'request_key')::uuid;
  if found then
    if event_record.actor_id is distinct from actor or event_record.request<>args then raise exception using errcode='PT409',message='appeal_key_conflict'; end if;
    return event_record.result;
  end if;
  if a.version<>(args->>'expected_version')::integer or a.status<>'received' then
    raise exception using errcode='PT409',message='appeal_version_conflict'; end if;
  update public.core_appeals set status=args->>'status',response=btrim(args->>'response'),version=version+1 where id=a.id returning * into a;
  result:=jsonb_build_object('id',a.id,'status',a.status,'response',a.response,'version',a.version);
  insert into public.core_appeal_events(request_key,appeal_id,actor_id,request,result)
    values((args->>'request_key')::uuid,a.id,actor,args,result);
  -- Resolving an appeal records the review; it never restores membership or consent.
  return result;
exception when invalid_text_representation or numeric_value_out_of_range or unique_violation then
  raise exception using errcode='PT422',message='invalid_appeal_input';
end; $$;
revoke all on function public.core_appeals(text,jsonb) from public,anon;
grant execute on function public.core_appeals(text,jsonb) to authenticated;
