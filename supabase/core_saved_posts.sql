-- Per-account hidden feed items and foreground deadline reminders; never sends push.
create table public.core_saved_post_preferences (
  owner_id uuid not null references public.core_profiles(id) on delete cascade,
  post_id uuid not null references public.core_posts(id) on delete cascade,
  hidden boolean not null default false,
  reminder boolean not null default false,
  updated_at timestamptz not null default clock_timestamp(),
  primary key(owner_id,post_id)
);
create index core_saved_post_preferences_post on public.core_saved_post_preferences(post_id);
alter table public.core_saved_post_preferences enable row level security;
revoke all on public.core_saved_post_preferences from public,anon,authenticated;
create policy core_saved_post_preferences_no_direct_access on public.core_saved_post_preferences
  for all to anon,authenticated using(false) with check(false);

create function core_private.saved_posts(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '8s' as $$
declare actor uuid:=auth.uid(); target uuid; p public.core_posts; result jsonb; allowed text[];
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false))
    or not exists(select 1 from auth.sessions where id=(auth.jwt()->>'session_id')::uuid and user_id=actor
      and (not_after is null or not_after>clock_timestamp())) then
    raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  allowed:=case action when 'list' then array[]::text[] when 'put' then array['post_id','hidden','reminder'] else null end;
  if allowed is null or jsonb_typeof(args) is distinct from 'object' or args-allowed<>'{}'::jsonb then
    raise exception using errcode='PT422',message='invalid_account_input'; end if;
  if action='put' then
    target:=(args->>'post_id')::uuid;
    if jsonb_typeof(args->'hidden') is distinct from 'boolean' or jsonb_typeof(args->'reminder') is distinct from 'boolean' then
      raise exception using errcode='PT422',message='invalid_account_input'; end if;
    perform pg_advisory_xact_lock(hashtextextended('core-saved-posts:'||actor::text,0));
    select * into p from public.core_posts where id=target;
    if not found then raise exception using errcode='PT404',message='post_not_found'; end if;
    if (args->>'reminder')::boolean and (p.cancelled or p.deadline_at<=clock_timestamp()) then
      raise exception using errcode='PT409',message='reminder_post_closed'; end if;
    if not exists(select 1 from public.core_saved_post_preferences where owner_id=actor and post_id=target)
      and (select count(*) from public.core_saved_post_preferences where owner_id=actor)>=500 then
      raise exception using errcode='PT409',message='saved_post_limit'; end if;
    insert into public.core_saved_post_preferences(owner_id,post_id,hidden,reminder)
      values(actor,target,(args->>'hidden')::boolean,(args->>'reminder')::boolean)
      on conflict(owner_id,post_id) do update set hidden=excluded.hidden,reminder=excluded.reminder,updated_at=clock_timestamp();
  end if;
  select coalesce(jsonb_agg(jsonb_build_object('post_id',p.id,'hidden',s.hidden,'reminder',s.reminder,
    'reminder_at',p.deadline_at-interval '30 minutes','deadline_at',p.deadline_at,
    'post_content',p.payload->>'content','status',case when p.cancelled then 'cancelled'
      when p.deadline_at<=clock_timestamp() then 'expired'
      when p.deadline_at-interval '30 minutes'<=clock_timestamp() then 'due' else 'scheduled' end)
    order by p.deadline_at,p.id),'[]') into result
    from public.core_saved_post_preferences s join public.core_posts p on p.id=s.post_id
    where s.owner_id=actor and (action='list' or p.id=target);
  if action='put' then return result->0; end if;
  return jsonb_build_object('items',result);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range then
  raise exception using errcode='PT422',message='invalid_account_input';
end;
$$;
revoke all on function core_private.saved_posts(text,jsonb) from public,anon,authenticated;
grant execute on function core_private.saved_posts(text,jsonb) to authenticated;
create function public.core_saved_posts(action text,args jsonb default '{}') returns jsonb
language sql security invoker set search_path = '' as $$ select core_private.saved_posts(action,args); $$;
revoke all on function public.core_saved_posts(text,jsonb) from public,anon,authenticated;
grant execute on function public.core_saved_posts(text,jsonb) to authenticated;
