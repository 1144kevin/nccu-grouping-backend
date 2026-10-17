-- Additive schema for the Supabase development adapter. No existing tables are changed.
-- Application writes go through one transaction RPC; identity always comes from auth.uid().
create schema core_private;
revoke all on schema core_private from public, anon, authenticated;

create table public.core_profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text not null check (char_length(btrim(display_name)) between 1 and 40),
  bio text not null default '' check (char_length(bio) <= 160),
  badge text not null default '🍱' check (badge in ('🍜','🥐','🍕','🧋','🍱','🍍')),
  version integer not null default 1 check (version >= 1),
  created_at timestamptz not null default now()
);
create table public.core_posts (
  id uuid primary key default gen_random_uuid(),
  author_id uuid not null references public.core_profiles(id),
  payload jsonb not null,
  deadline_at timestamptz not null,
  capacity integer not null check (capacity between 2 and 20),
  cancelled boolean not null default false,
  version integer not null default 1 check (version >= 1),
  request_key text not null check (char_length(request_key) between 12 and 100),
  original_payload jsonb not null,
  created_at timestamptz not null default clock_timestamp(),
  unique (author_id, request_key)
);
create table public.core_join_requests (
  id uuid primary key default gen_random_uuid(),
  post_id uuid not null references public.core_posts(id),
  requester_id uuid not null references public.core_profiles(id),
  status text not null check (status in ('pending','accepted','rejected','cancelled')),
  created_at timestamptz not null default clock_timestamp(),
  decided_at timestamptz,
  unique (post_id, requester_id)
);
create index core_posts_feed on public.core_posts(created_at desc, id desc);
create index core_posts_author on public.core_posts(author_id, created_at desc, id desc);
create index core_requests_post_status on public.core_join_requests(post_id, status);
create index core_requests_user_status on public.core_join_requests(requester_id, status, post_id);

alter table public.core_profiles enable row level security;
alter table public.core_posts enable row level security;
alter table public.core_join_requests enable row level security;
revoke all on public.core_profiles, public.core_posts, public.core_join_requests from anon, authenticated;
grant select on public.core_profiles, public.core_join_requests to authenticated;
grant select (id,author_id,payload,deadline_at,capacity,cancelled,version,created_at) on public.core_posts to authenticated;
create policy core_profiles_read on public.core_profiles for select to authenticated using ((select auth.uid()) is not null);
create policy core_posts_read on public.core_posts for select to authenticated using ((select auth.uid()) is not null);
create policy core_requests_read on public.core_join_requests for select to authenticated using (
  requester_id = (select auth.uid()) or exists (
    select 1 from public.core_posts p where p.id = post_id and p.author_id = (select auth.uid())
  )
);

create function core_private.profile_view(actor uuid) returns jsonb
language sql stable set search_path = '' as $$
  select jsonb_build_object('id', p.id, 'display_name', p.display_name, 'bio', p.bio,
    'badge', p.badge, 'version', p.version, 'created_at', p.created_at,
    'email', u.email, 'email_verified', u.email_confirmed_at is not null, 'auth_mode', 'supabase')
  from public.core_profiles p join auth.users u on u.id=p.id where p.id=actor;
$$;
create function core_private.post_view(target_id uuid, actor uuid) returns jsonb
language sql stable set search_path = '' as $$
  select post.payload || jsonb_build_object(
    'id', post.id, 'author_id', post.author_id, 'author_name', p.display_name, 'author_badge', p.badge,
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
create function core_private.join_view(request public.core_join_requests) returns jsonb
language sql stable set search_path = '' as $$
  select to_jsonb(request) || jsonb_build_object('requester_name',p.display_name)
  from public.core_profiles p where p.id=request.requester_id;
$$;

-- The public RPC can be called without FastAPI. Validate all write input again in Postgres.
create function core_private.post_input(value jsonb) returns jsonb
language plpgsql set search_path = '' as $$
declare deadline timestamptz; capacity integer; price numeric; tags jsonb; loc jsonb;
begin
  if jsonb_typeof(value) is distinct from 'object' or
    value - array['content','tags','deadline_at','capacity','location','price_per_person'] <> '{}'::jsonb or
    jsonb_typeof(value->'content') is distinct from 'string' or
    char_length(btrim(value->>'content')) not between 10 and 500 or
    jsonb_typeof(value->'tags') is distinct from 'array' or
    jsonb_array_length(value->'tags') not between 1 and 10 or
    jsonb_typeof(value->'capacity') is distinct from 'number' or
    (value->>'capacity') !~ '^[0-9]+$' or
    jsonb_typeof(value->'deadline_at') is distinct from 'string' or
    (value->>'deadline_at') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
  then raise exception using errcode='PT422', message='invalid_post_input'; end if;
  if exists (select 1 from jsonb_array_elements(value->'tags') t where jsonb_typeof(t) <> 'string'
    or char_length(btrim(t #>> '{}')) not between 1 and 40)
  then raise exception using errcode='PT422', message='invalid_tag'; end if;
  select jsonb_agg(tag order by first_index) into tags from (
    select btrim(t #>> '{}') as tag,min(n) as first_index
    from jsonb_array_elements(value->'tags') with ordinality as x(t,n) group by btrim(t #>> '{}')
  ) cleaned;
  capacity := (value->>'capacity')::integer;
  deadline := (value->>'deadline_at')::timestamptz;
  if capacity not between 2 and 20 then raise exception using errcode='PT422',message='invalid_capacity'; end if;
  if deadline<=clock_timestamp() or deadline>clock_timestamp()+interval '14 days'
  then raise exception using errcode='PT422',message='deadline_must_be_within_14_days'; end if;
  loc := value->'location';
  if jsonb_typeof(loc) is distinct from 'object' or
    loc - array['name','latitude','longitude','address','source'] <> '{}'::jsonb or
    jsonb_typeof(loc->'name') is distinct from 'string' or char_length(btrim(loc->>'name')) not between 1 and 100 or
    jsonb_typeof(loc->'latitude') is distinct from 'number' or
    jsonb_typeof(loc->'longitude') is distinct from 'number' or
    (loc->>'latitude')::numeric not between -90 and 90 or (loc->>'longitude')::numeric not between -180 and 180 or
    (loc->'address' is not null and loc->'address'<>'null'::jsonb and
      (jsonb_typeof(loc->'address')<>'string' or char_length(loc->>'address')>500)) or
    (loc->'source' is not null and loc->'source'<>'null'::jsonb and loc->>'source'<>'openstreetmap')
  then raise exception using errcode='PT422',message='invalid_location'; end if;
  if value->'price_per_person' is not null and value->'price_per_person'<>'null'::jsonb then
    if jsonb_typeof(value->'price_per_person') not in ('number','string') or
      (value->>'price_per_person') !~ '^[0-9]{1,6}(\.[0-9]{1,2})?$'
    then raise exception using errcode='PT422',message='invalid_price'; end if;
    price := (value->>'price_per_person')::numeric;
  end if;
  return jsonb_build_object('content',btrim(value->>'content'),'tags',tags,'capacity',capacity,
    'deadline_at',deadline,'location',loc || jsonb_build_object('name',btrim(loc->>'name')),
    'price_per_person',case when price is null then null else to_char(price,'FM999999990.00') end);
end;
$$;

create function core_private.dispatch(action text, args jsonb) returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '8s' as $$
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
    if char_length(coalesce(args->>'request_key','')) not between 12 and 100 then
      raise exception using errcode='PT422',message='invalid_request_key'; end if;
    -- Serialize matching create retries by owner, then enforce a unique owner/key pair.
    perform 1 from public.core_profiles where id=actor for update;
    select * into post from public.core_posts where author_id=actor and request_key=args->>'request_key';
    if found then
      if post.original_payload <> args->'value' then
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
exception when invalid_text_representation or invalid_datetime_format or datetime_field_overflow
  or numeric_value_out_of_range or check_violation or not_null_violation then
  raise exception using errcode='PT422',message='invalid_input';
end;
$$;
revoke all on all functions in schema core_private from public, anon, authenticated;
grant usage on schema core_private to authenticated;
grant execute on function core_private.dispatch(text,jsonb) to authenticated;

create function public.core_dispatch(action text, args jsonb default '{}') returns jsonb
language sql security invoker set search_path = '' as $$
  select core_private.dispatch(action,args);
$$;
revoke all on function public.core_dispatch(text,jsonb) from public, anon;
grant execute on function public.core_dispatch(text,jsonb) to authenticated;
comment on function public.core_dispatch(text,jsonb) is 'Core grouping API: authenticated identity, parent-row locks, validated input; no direct client writes.';
