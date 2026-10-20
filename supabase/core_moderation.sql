-- Prepared migration, not automatically applied. No operator is granted by this file.
begin;
create table public.core_moderators (
  user_id uuid primary key references auth.users(id) on delete cascade,
  enabled boolean not null default false,
  expires_at timestamptz not null,
  created_at timestamptz not null default clock_timestamp()
);
alter table public.core_moderators enable row level security;
revoke all on public.core_moderators from public,anon,authenticated;
alter table public.core_reports drop constraint core_reports_status_check;
alter table public.core_reports add constraint core_reports_status_check
  check(status in ('received','reviewing','resolved','dismissed'));
alter table public.core_reports add column review_version integer not null default 1 check(review_version>0),
  add column public_response text not null default '' check(char_length(public_response)<=300);
create table public.core_moderation_events (
  id uuid primary key default gen_random_uuid(),
  report_id uuid not null references public.core_reports(id) on delete cascade,
  actor_id uuid references auth.users(id) on delete set null,
  request_key uuid not null unique,
  request jsonb not null,
  result jsonb not null,
  created_at timestamptz not null default clock_timestamp()
);
alter table public.core_moderation_events enable row level security;
revoke all on public.core_moderation_events from public,anon,authenticated;

create function public.core_moderation(action text,args jsonb default '{}') returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=auth.uid(); r public.core_reports; e public.core_moderation_events;
  result jsonb; state text; operation text; post_id uuid; affected integer;
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false)
    and (banned_until is null or banned_until<clock_timestamp()))
    or not exists(select 1 from auth.sessions where user_id=actor and id=(auth.jwt()->>'session_id')::uuid)
    then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  if jsonb_typeof(args) is distinct from 'object' then
    raise exception using errcode='PT422',message='invalid_moderation_input'; end if;
  if action='mine' and args='{}' then
    select coalesce(jsonb_agg(payload||jsonb_build_object('id',id,'status',status,'created_at',created_at,
      'public_response',public_response,'review_version',review_version) order by created_at desc,id desc),'[]')
      into result from (select * from public.core_reports where owner_id=actor order by created_at desc,id desc limit 50) q;
    return jsonb_build_object('items',result);
  end if;
  -- Never trust client-editable user_metadata, email domains, or caller-provided actor IDs.
  if not exists(select 1 from public.core_moderators where user_id=actor and enabled and expires_at>clock_timestamp())
    then raise exception using errcode='PT403',message='moderator_required'; end if;
  if action='list' and args='{}' then
    select coalesce(jsonb_agg(to_jsonb(q) order by created_at desc,id desc),'[]') into result
      from(select id,owner_id,payload,status,review_version,public_response,created_at from public.core_reports
        order by created_at desc,id desc limit 50) q;
    return jsonb_build_object('items',result);
  end if;
  if action<>'review' or args-array['report_id','expected_version','action','public_response','internal_note','request_key']<>'{}'
    or jsonb_typeof(args->'expected_version') is distinct from 'number' or args->>'expected_version' !~ '^[1-9][0-9]{0,8}$'
    or not coalesce(args->>'action' in ('review','resolve','dismiss','cancel_post'),false)
    or jsonb_typeof(args->'public_response') is distinct from 'string'
    or char_length(btrim(args->>'public_response')) not between 5 and 300
    or jsonb_typeof(args->'internal_note') is distinct from 'string' or char_length(args->>'internal_note')>1000
    or not coalesce(args->>'request_key' ~ '^[0-9a-fA-F-]{36}$',false)
    or not coalesce(args->>'report_id' ~ '^[0-9a-fA-F-]{36}$',false)
    then raise exception using errcode='PT422',message='invalid_moderation_input'; end if;
  -- Lock report before replay lookup: concurrent identical retries return the same result.
  perform pg_advisory_xact_lock(hashtextextended(args->>'request_key',0));
  select * into r from public.core_reports where id=(args->>'report_id')::uuid for update;
  if not found then raise exception using errcode='PT404',message='report_not_found'; end if;
  select * into e from public.core_moderation_events where request_key=(args->>'request_key')::uuid;
  if found then
    if e.actor_id is distinct from actor or e.request<>args then
      raise exception using errcode='PT409',message='moderation_key_conflict'; end if;
    return e.result;
  end if;
  if r.review_version<>(args->>'expected_version')::integer then
    raise exception using errcode='PT409',message='report_version_conflict'; end if;
  operation:=args->>'action';
  if r.status in ('resolved','dismissed') or (operation='review' and r.status<>'received') then
    raise exception using errcode='PT409',message='report_already_reviewed'; end if;
  if operation<>'review' and r.status<>'reviewing' then
    raise exception using errcode='PT409',message='review_required'; end if;
  if operation='cancel_post' then
    if r.payload->>'kind'<>'post' then raise exception using errcode='PT422',message='post_report_required'; end if;
    post_id:=(r.payload->>'target_id')::uuid;
    update public.core_posts set cancelled=true,version=version+1 where id=post_id and not cancelled;
    get diagnostics affected=row_count;
    if affected<>1 then raise exception using errcode='PT409',message='post_unavailable'; end if;
  end if;
  state:=case operation when 'review' then 'reviewing' when 'dismiss' then 'dismissed' else 'resolved' end;
  update public.core_reports set status=state,public_response=btrim(args->>'public_response'),
    review_version=review_version+1 where id=r.id returning * into r;
  result:=jsonb_build_object('id',r.id,'status',r.status,'review_version',r.review_version,
    'public_response',r.public_response,'cancelled_post_id',post_id);
  insert into public.core_moderation_events(report_id,actor_id,request_key,request,result)
    values(r.id,actor,(args->>'request_key')::uuid,args,result);
  return result;
end; $$;
revoke all on function public.core_moderation(text,jsonb) from public,anon;
grant execute on function public.core_moderation(text,jsonb) to authenticated;
commit;
