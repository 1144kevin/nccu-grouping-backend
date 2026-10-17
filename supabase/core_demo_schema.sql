-- Explicit provenance for development samples; ordinary app writes cannot set it.
alter table public.core_posts add column demo_batch text;
alter table public.core_posts add column demo_owner_id uuid references public.core_profiles(id);
alter table public.core_posts add constraint core_demo_provenance check (
  (demo_batch is null) = (demo_owner_id is null)
);
create table core_private.demo_batches (
  batch text primary key,
  owner_id uuid not null references public.core_profiles(id),
  people uuid[] not null,
  created_at timestamptz not null default now()
);
alter table core_private.demo_batches enable row level security;
revoke all on core_private.demo_batches from public, anon, authenticated;

create or replace function core_private.post_view(target_id uuid, actor uuid) returns jsonb
language sql stable set search_path = '' as $$
  select post.payload || jsonb_build_object(
    'id', post.id, 'author_id', post.author_id, 'author_name', p.display_name, 'author_badge', p.badge,
    'is_demo', post.demo_batch is not null,
    'created_at', post.created_at, 'version', post.version, 'joined', 1 + counts.accepted,
    'status', case when post.cancelled then 'cancelled' when post.deadline_at<=clock_timestamp() then 'expired'
      when 1+counts.accepted>=post.capacity then 'full' else 'open' end,
    'my_status', case when actor=post.author_id then 'host' else coalesce(j.status,'none') end,
    'can_edit', actor=post.author_id and not post.cancelled and post.deadline_at>clock_timestamp() and counts.active=0)
  from public.core_posts post join public.core_profiles p on p.id=post.author_id
  cross join lateral (select count(*) filter (where status='accepted') as accepted,
    count(*) filter (where status in ('pending','accepted')) as active
    from public.core_join_requests where post_id=post.id) counts
  left join public.core_join_requests j on j.post_id=post.id and j.requester_id=actor
  where post.id=target_id;
$$;

create function core_private.clear_demo() returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '8s' as $$
declare actor uuid := auth.uid(); target public.core_posts; removed integer := 0; kept integer := 0;
begin
  if actor is null or not exists (
    select 1 from auth.sessions where user_id=actor and id=(auth.jwt()->>'session_id')::uuid
      and (not_after is null or not_after>clock_timestamp())
  ) then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  -- Lock each parent before examining/deleting requests, matching normal join operations.
  for target in select p.* from public.core_posts p
    join core_private.demo_batches b on b.batch=p.demo_batch and b.owner_id=p.demo_owner_id
    where p.demo_owner_id=actor order by p.id for update of p
  loop
    if exists (
      select 1 from public.core_join_requests j
      join core_private.demo_batches b on b.batch=target.demo_batch
      where j.post_id=target.id and j.requester_id<>actor and not (j.requester_id=any(b.people))
    ) then kept := kept+1; continue; end if;
    delete from public.core_join_requests where post_id=target.id;
    delete from public.core_posts where id=target.id;
    removed := removed+1;
  end loop;
  return jsonb_build_object('deleted_posts',removed,'kept_posts',kept);
end;
$$;
revoke all on function core_private.clear_demo() from public,anon,authenticated;
grant execute on function core_private.clear_demo() to authenticated;
create function public.core_clear_demo() returns jsonb
language sql security invoker set search_path = '' as $$ select core_private.clear_demo(); $$;
revoke all on function public.core_clear_demo() from public,anon,authenticated;
grant execute on function public.core_clear_demo() to authenticated;
