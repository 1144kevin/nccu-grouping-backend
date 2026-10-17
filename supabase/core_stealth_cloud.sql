-- Additive cloud adapter. Existing core grouping and local stealth stay unchanged.
create table public.core_stealth_agents (
  owner_id uuid primary key references public.core_profiles(id),
  payload jsonb not null,
  version integer not null default 1 check(version>0),
  policy_version integer not null default 1 check(policy_version>0),
  last_run_at timestamptz,
  updated_at timestamptz not null default clock_timestamp()
);
create table public.core_stealth_signals (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references public.core_stealth_agents(owner_id),
  payload jsonb not null,
  status text not null default 'active' check(status in ('active','revoked')),
  version integer not null default 1 check(version>0),
  request_key uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  unique(owner_id,request_key)
);
create index core_stealth_signals_owner on public.core_stealth_signals(owner_id,created_at desc,id);
create index core_stealth_signals_candidate on public.core_stealth_signals((payload->>'location_id'),created_at desc) where status='active';
create table public.core_stealth_proposals (
  id uuid primary key default gen_random_uuid(),
  fingerprint text not null unique,
  owners uuid[] not null check(cardinality(owners) between 2 and 3),
  signals uuid[] not null check(cardinality(signals)=cardinality(owners)),
  signal_versions integer[] not null,
  policy_versions integer[] not null,
  payload jsonb not null,
  decisions jsonb not null default '{}',
  created_at timestamptz not null default clock_timestamp()
);
create index core_stealth_proposals_owners on public.core_stealth_proposals using gin(owners);
create index core_stealth_proposals_created on public.core_stealth_proposals(created_at desc,id);
alter table public.core_stealth_agents enable row level security;
alter table public.core_stealth_signals enable row level security;
alter table public.core_stealth_proposals enable row level security;
revoke all on public.core_stealth_agents,public.core_stealth_signals,public.core_stealth_proposals from public,anon,authenticated;
create policy core_stealth_agents_rpc_only on public.core_stealth_agents for all to anon,authenticated using(false) with check(false);
create policy core_stealth_signals_rpc_only on public.core_stealth_signals for all to anon,authenticated using(false) with check(false);
create policy core_stealth_proposals_rpc_only on public.core_stealth_proposals for all to anon,authenticated using(false) with check(false);

create function core_private.stealth_validate(kind text,v jsonb) returns void
language plpgsql set search_path='' as $$
declare key text; allowed text[]; item jsonb; n numeric; st timestamptz; en timestamptz; ex timestamptz;
begin
  if jsonb_typeof(v) is distinct from 'object' then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
  if kind='agent' then
    allowed:=array['name','enabled','daily_invite_limit','timezone','quiet_start_hour','quiet_end_hour','exploration','tone','avatar_key'];
    if v-allowed<>'{}' or not v ?& allowed or jsonb_typeof(v->'name')<>'string' or char_length(btrim(v->>'name')) not between 1 and 40
      or jsonb_typeof(v->'enabled')<>'boolean' or jsonb_typeof(v->'daily_invite_limit')<>'number'
      or (v->>'daily_invite_limit') !~ '^[0-9]+$' or (v->>'daily_invite_limit')::integer not between 1 and 10
      or not exists(select 1 from pg_catalog.pg_timezone_names where name=v->>'timezone')
      or v->>'exploration' not in ('familiar','balanced') or v->>'tone' not in ('direct','warm','playful')
      or v->>'avatar_key' not in ('rice','pizza','tea','cat') then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
    foreach key in array array['timezone','exploration','tone','avatar_key'] loop
      if jsonb_typeof(v->key)<>'string' then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
    end loop;
    if (v->'quiet_start_hour'='null')<>(v->'quiet_end_hour'='null') then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
    if v->'quiet_start_hour'<>'null' then
      foreach key in array array['quiet_start_hour','quiet_end_hour'] loop
        if jsonb_typeof(v->key)<>'number' or v->>key !~ '^[0-9]+$' or (v->>key)::integer not between 0 and 23 then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
      end loop;
      if v->'quiet_start_hour'=v->'quiet_end_hour' then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
    end if;
    return;
  end if;
  allowed:=array['kind','category','acceptable_categories','title','location_id','starts_at','ends_at','expires_at','budget_max_twd','known_cost_twd','tags','dietary_requirements','dietary_verified','roles'];
  if v-allowed<>'{}' or not v ?& allowed or v->>'kind' not in ('need','offer','plan')
    or v->>'category' not in ('pizza','meal','drink','bakery','snack') or v->>'location_id' not in ('east_gate','main_gate','south_gate')
    or char_length(btrim(v->>'title')) not between 1 and 160 then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
  foreach key in array array['kind','category','title','location_id','starts_at','ends_at','expires_at'] loop
    if jsonb_typeof(v->key)<>'string' then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
  end loop;
  foreach key in array array['starts_at','ends_at','expires_at'] loop
    if v->>key !~ '(Z|z|[+-][0-9]{2}:[0-9]{2})$' then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
  end loop;
  st:=(v->>'starts_at')::timestamptz; en:=(v->>'ends_at')::timestamptz; ex:=(v->>'expires_at')::timestamptz;
  if st>=en or en-st>interval '24 hours' or ex>en or ex<=clock_timestamp() or en<=clock_timestamp() or en>clock_timestamp()+interval '7 days' then
    raise exception using errcode='PT422',message='invalid_signal_window'; end if;
  foreach key in array array['budget_max_twd','known_cost_twd'] loop
    if v->key<>'null' then
      if jsonb_typeof(v->key)<>'number' or v->>key !~ '^[0-9]+$' then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
      n:=(v->>key)::numeric; if n<0 or n>100000 then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
    end if;
  end loop;
  foreach key in array array['tags','dietary_requirements','dietary_verified','roles','acceptable_categories'] loop
    if jsonb_typeof(v->key)<>'array' or jsonb_array_length(v->key)>(case key when 'roles' then 4 when 'acceptable_categories' then 5 else 12 end) then
      raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
    for item in select value from jsonb_array_elements(v->key) loop
      if jsonb_typeof(item)<>'string' or char_length(btrim(item#>>'{}')) not between 1 and 32 then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
      if key='roles' and item#>>'{}' not in ('order','pickup','coupon','share') then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
      if key='acceptable_categories' and item#>>'{}' not in ('pizza','meal','drink','bakery','snack') then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
    end loop;
  end loop;
  if (v->>'kind'='plan' and not v->'roles' ? 'pickup') or (v->>'kind'='offer' and (jsonb_array_length(v->'roles')=0 or jsonb_array_length(v->'acceptable_categories')>0)) then
    raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
end; $$;

create function core_private.stealth_awake(v jsonb,ts timestamptz) returns boolean
language sql stable set search_path='' as $$
  select (v->>'enabled')::boolean and (v->'quiet_start_hour'='null' or not (
    case when (v->>'quiet_start_hour')::integer<(v->>'quiet_end_hour')::integer then
      extract(hour from ts at time zone (v->>'timezone'))>=(v->>'quiet_start_hour')::integer and extract(hour from ts at time zone (v->>'timezone'))<(v->>'quiet_end_hour')::integer
    else extract(hour from ts at time zone (v->>'timezone'))>=(v->>'quiet_start_hour')::integer or extract(hour from ts at time zone (v->>'timezone'))<(v->>'quiet_end_hour')::integer end));
$$;

-- Reads only internally. No caller can retrieve another person's private signal.
create function core_private.stealth_candidate(ids uuid[],ts timestamptz) returns jsonb
language plpgsql set search_path='' as $$
declare n integer:=cardinality(ids); owners uuid[]; versions integer[]; policies integer[]; cats text[]; chosen text; roles text[]; tags text[]; reqs text[];
  st timestamptz; en timestamptz; ex timestamptz; budget integer; cost integer; kinds integer; offers integer; plans integer; reasons jsonb; unknowns jsonb:='[]'; fp text;
begin
  if n not between 2 and 3 or (select count(*) from public.core_stealth_signals where id=any(ids))<>n then return null; end if;
  if (select count(distinct owner_id) from public.core_stealth_signals where id=any(ids))<>n then return null; end if;
  if exists(select 1 from public.core_stealth_signals s join public.core_stealth_agents a on a.owner_id=s.owner_id where s.id=any(ids)
    and (s.status<>'active' or (s.payload->>'expires_at')::timestamptz<=ts or not core_private.stealth_awake(a.payload,ts))) then return null; end if;
  if not exists(select 1 from public.core_stealth_signals where id=any(ids) and payload->>'kind'='need') then return null; end if;
  if (select count(distinct payload->>'location_id') from public.core_stealth_signals where id=any(ids))<>1 then return null; end if;
  select array_agg(c order by c) into cats from unnest(array['pizza','meal','drink','bakery','snack']) c where not exists (
    select 1 from public.core_stealth_signals s where s.id=any(ids) and s.payload->>'category'<>c and not s.payload->'acceptable_categories' ? c);
  if coalesce(cardinality(cats),0)=0 then return null; end if;
  select coalesce(min(payload->>'category') filter(where payload->>'kind'='offer'),min(cats[1])) into chosen from public.core_stealth_signals where id=any(ids);
  select greatest(ts,max((payload->>'starts_at')::timestamptz)),min((payload->>'ends_at')::timestamptz),min((payload->>'expires_at')::timestamptz),
    min((payload->>'budget_max_twd')::integer),min((payload->>'known_cost_twd')::integer),count(distinct payload->>'kind'),
    count(*) filter(where payload->>'kind'='offer'),count(*) filter(where payload->>'kind'='plan')
  into st,en,ex,budget,cost,kinds,offers,plans from public.core_stealth_signals where id=any(ids);
  ex:=least(ex,ts+interval '30 minutes',case when st=ts then en else st end);
  if st>=en or ex<=ts or (cost is not null and budget is not null and cost>budget) or
    (select count(distinct payload->>'known_cost_twd') from public.core_stealth_signals where id=any(ids))>1 then return null; end if;
  select array_agg(distinct value) into roles from public.core_stealth_signals s cross join lateral jsonb_array_elements_text(s.payload->'roles') where s.id=any(ids);
  select array_agg(distinct value) into reqs from public.core_stealth_signals s cross join lateral jsonb_array_elements_text(s.payload->'dietary_requirements') where s.id=any(ids);
  if reqs is not null and exists(select 1 from public.core_stealth_signals s where s.id=any(ids) and s.payload->>'kind'<>'plan'
    and not s.payload->'dietary_verified' @> to_jsonb(reqs)) then return null; end if;
  select array_agg(value) into tags from (select value from public.core_stealth_signals s cross join lateral jsonb_array_elements_text(s.payload->'tags') where s.id=any(ids)
    group by value having count(distinct s.id)=n) shared;
  if kinds=1 and offers=0 and plans=0 and coalesce(cardinality(tags),0)=0 and exists(
    select 1 from public.core_stealth_signals s join public.core_stealth_agents a on a.owner_id=s.owner_id where s.id=any(ids) and a.payload->>'exploration'='familiar') then return null; end if;
  select array_agg(s.owner_id order by s.owner_id),array_agg(s.id order by s.owner_id),array_agg(s.version order by s.owner_id),array_agg(a.policy_version order by s.owner_id),
    encode(sha256(convert_to(string_agg(s.id::text||':'||s.version||':'||a.policy_version,'|' order by s.id),'UTF8')),'hex')
    into owners,ids,versions,policies,fp from public.core_stealth_signals s join public.core_stealth_agents a on a.owner_id=s.owner_id where s.id=any(ids);
  reasons:=jsonb_build_array('取餐地點一致','可用時間有交集','餐點分類符合本人確認的選項');
  if cardinality(tags)>0 then reasons:=reasons||jsonb_build_array('有共同的餐點偏好'); end if;
  if offers>0 then reasons:=reasons||jsonb_build_array('有人願意提供資源'); end if;
  if plans>0 then reasons:=reasons||jsonb_build_array('有人明確表示願意順路取餐'); end if;
  if cost is null then unknowns:=unknowns||jsonb_build_array('實際每人費用尚未確認'); end if;
  if not coalesce('order'=any(roles),false) then unknowns:=unknowns||jsonb_build_array('尚需確認下單者'); end if;
  if not coalesce('pickup'=any(roles),false) then unknowns:=unknowns||jsonb_build_array('尚需確認取餐者'); end if;
  if 'coupon'=any(roles) then unknowns:=unknowns||jsonb_build_array('優惠券有效性與使用條件需由持有人確認'); end if;
  if reqs is not null then unknowns:=unknowns||jsonb_build_array('飲食資訊為參與者提供，仍須向商家確認；不保證過敏安全'); end if;
  return jsonb_build_object('owners',owners,'signals',ids,'signal_versions',versions,'policy_versions',policies,'fingerprint',fp,'payload',jsonb_build_object(
    'category',chosen,'location_id',(select payload->>'location_id' from public.core_stealth_signals where id=ids[1]),'starts_at',st,'ends_at',en,'expires_at',ex,
    'budget_max_twd',budget,'known_cost_twd',cost,'member_count',n,'score',least(100,40+10*coalesce(cardinality(tags),0)+10*(kinds-1)+5*coalesce(cardinality(roles),0)+5*(n-2)),
    'reasons',reasons,'unknowns',unknowns));
end; $$;

create function core_private.stealth_view(p public.core_stealth_proposals,actor uuid) returns jsonb
language plpgsql stable set search_path='' as $$
declare valid boolean:=true; i integer; own_signal jsonb; own_agent jsonb; decision text; state text;
begin
  if not actor=any(p.owners) then return null; end if;
  if (p.payload->>'expires_at')::timestamptz<=clock_timestamp() then valid:=false; end if;
  for i in 1..cardinality(p.owners) loop
    if not exists(select 1 from public.core_stealth_signals s join public.core_stealth_agents a on a.owner_id=s.owner_id
      where s.id=p.signals[i] and s.owner_id=p.owners[i] and s.status='active' and s.version=p.signal_versions[i]
      and (s.payload->>'expires_at')::timestamptz>clock_timestamp() and (a.payload->>'enabled')::boolean and a.policy_version=p.policy_versions[i]) then valid:=false; end if;
  end loop;
  decision:=coalesce(p.decisions->>actor::text,'pending');
  state:=case when not valid or exists(select 1 from jsonb_each_text(p.decisions) where value='declined') then 'unavailable'
    when (select count(*) from jsonb_each_text(p.decisions) where value='accepted')=cardinality(p.owners) then 'ready' else 'pending' end;
  select payload into own_signal from public.core_stealth_signals where id=p.signals[array_position(p.owners,actor)];
  select payload into own_agent from public.core_stealth_agents where owner_id=actor;
  return p.payload||jsonb_build_object('id',p.id,'created_at',p.created_at,'my_decision',decision,'status',state,'my_signal_title',own_signal->>'title','my_roles',own_signal->'roles',
    'invitation_text',case own_agent->>'tone' when 'playful' then '分身幫你找到一組可以一起揪的人！' when 'direct' then '找到符合條件的互補配對。' else '有一組時間與需求合得來的夥伴，想一起看看嗎？' end,
    'why_me',jsonb_build_array('你的需求類型、取餐地點與時間符合這個方案','只使用你已確認的條件，沒有替你承諾加入'),'is_llm',false);
end; $$;

create function core_private.stealth_cloud(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=auth.uid(); allowed text[]; v jsonb; a public.core_stealth_agents; s public.core_stealth_signals; p public.core_stealth_proposals;
  previous public.core_stealth_agents; ts timestamptz:=clock_timestamp(); own public.core_stealth_signals; c record; d record;
  candidate jsonb; result jsonb; created integer:=0; added integer; maximum integer; owners uuid[]; ids uuid[]; versions integer[]; policies integer[]; policy_changed boolean;
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false)) or
    not exists(select 1 from auth.sessions where user_id=actor and id=(auth.jwt()->>'session_id')::uuid and (not_after is null or not_after>ts)) then
    raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  allowed:=case action when 'agent_get' then array[]::text[] when 'agent_put' then array['value','expected_version']
    when 'signal_create' then array['value','request_key'] when 'signals' then array[]::text[] when 'revoke' then array['signal_id']
    when 'run' then array['max_results'] when 'matches' then array[]::text[] when 'decision' then array['proposal_id','decision'] else null end;
  if allowed is null or jsonb_typeof(args) is distinct from 'object' or args-allowed<>'{}' then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
  -- Dedicated feature lock only; core grouping/chat operations never take it.
  if action in ('agent_put','signal_create','revoke','run','decision') and not pg_try_advisory_xact_lock(hashtextextended('core_stealth_dev_v1',0)) then
    raise exception using errcode='PT409',message='stealth_busy_retry'; end if;
  select * into a from public.core_stealth_agents where owner_id=actor;
  if action='agent_get' then
    if not found then return 'null'::jsonb; end if;
    return a.payload||jsonb_build_object('user_id',actor,'version',a.version,'policy_version',a.policy_version,'updated_at',a.updated_at);
  elsif action='agent_put' then
    v:=args->'value'; perform core_private.stealth_validate('agent',v);
    if jsonb_typeof(args->'expected_version') is distinct from 'number' or args->>'expected_version' !~ '^[0-9]+$' then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
    if coalesce(a.version,0)<>(args->>'expected_version')::integer then raise exception using errcode='PT409',message='agent_version_conflict'; end if;
    policy_changed:=a.owner_id is null or a.payload->'enabled'<>v->'enabled' or a.payload->'exploration'<>v->'exploration';
    insert into public.core_stealth_agents(owner_id,payload,version,policy_version) values(actor,v,1,1)
      on conflict(owner_id) do update set payload=excluded.payload,version=core_stealth_agents.version+1,
      policy_version=core_stealth_agents.policy_version+policy_changed::integer,updated_at=ts returning * into a;
    return a.payload||jsonb_build_object('user_id',actor,'version',a.version,'policy_version',a.policy_version,'updated_at',a.updated_at);
  elsif action='signal_create' then
    if a.owner_id is null then raise exception using errcode='PT409',message='create_agent_first'; end if;
    select * into s from public.core_stealth_signals where owner_id=actor and request_key=(args->>'request_key')::uuid;
    if found then
      if s.payload<>args->'value' then raise exception using errcode='PT409',message='idempotency_payload_conflict'; end if;
    else
      perform core_private.stealth_validate('signal',args->'value');
      if (select count(*) from public.core_stealth_signals where owner_id=actor and status='active' and (payload->>'expires_at')::timestamptz>ts)>=10 then
        raise exception using errcode='PT429',message='active_signal_limit'; end if;
      if args->>'request_key' is null then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
      insert into public.core_stealth_signals(owner_id,payload,request_key) values(actor,args->'value',(args->>'request_key')::uuid) returning * into s;
    end if;
    return s.payload||jsonb_build_object('id',s.id,'owner_id',actor,'status',s.status,'version',s.version,'created_at',s.created_at);
  elsif action='signals' then
    select coalesce(jsonb_agg(x.payload||jsonb_build_object('id',x.id,'owner_id',actor,'status',x.status,'version',x.version,'created_at',x.created_at) order by x.created_at desc,x.id desc),'[]') into result
      from (select * from public.core_stealth_signals where owner_id=actor order by created_at desc,id desc limit 50) x;
    return jsonb_build_object('items',result);
  elsif action='revoke' then
    update public.core_stealth_signals set status='revoked',version=version+case when status='active' then 1 else 0 end where id=(args->>'signal_id')::uuid and owner_id=actor returning * into s;
    if not found then raise exception using errcode='PT404',message='stealth_not_found'; end if;
    return s.payload||jsonb_build_object('id',s.id,'owner_id',actor,'status',s.status,'version',s.version,'created_at',s.created_at);
  elsif action='matches' then
    select coalesce(jsonb_agg(core_private.stealth_view(x,actor) order by x.created_at desc,x.id desc),'[]') into result from
      (select * from public.core_stealth_proposals where actor=any(owners) order by created_at desc,id desc limit 50) x;
    return jsonb_build_object('items',result);
  elsif action='decision' then
    if args->>'decision' is null or args->>'decision' not in ('accepted','declined') then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
    select * into p from public.core_stealth_proposals where id=(args->>'proposal_id')::uuid and actor=any(owners);
    if not found then raise exception using errcode='PT404',message='stealth_not_found'; end if;
    result:=core_private.stealth_view(p,actor);
    if result->>'status'='unavailable' then raise exception using errcode='PT409',message='proposal_unavailable'; end if;
    if coalesce(p.decisions->>actor::text,'pending')=args->>'decision' then return result; end if;
    update public.core_stealth_proposals set decisions=decisions||jsonb_build_object(actor::text,args->>'decision') where id=p.id returning * into p;
    return core_private.stealth_view(p,actor);
  end if;
  if a.owner_id is null or not (a.payload->>'enabled')::boolean then raise exception using errcode='PT409',message='agent_not_enabled'; end if;
  if not core_private.stealth_awake(a.payload,ts) then raise exception using errcode='PT409',message='agent_quiet_hours'; end if;
  if a.last_run_at>ts-interval '30 seconds' then raise exception using errcode='PT429',message='match_cooldown'; end if;
  maximum:=coalesce((args->>'max_results')::integer,3);
  if maximum not between 1 and 5 then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
  update public.core_stealth_agents set last_run_at=ts where owner_id=actor;
  -- At most 10 anchors and 20 compatible candidates per anchor, including triples.
  for own in select * from public.core_stealth_signals where owner_id=actor and status='active' and (payload->>'expires_at')::timestamptz>ts order by created_at desc,id limit 10 loop
    for c in select s.id from public.core_stealth_signals s join public.core_stealth_agents g on g.owner_id=s.owner_id where s.owner_id<>actor and s.status='active'
      and core_private.stealth_awake(g.payload,ts) and (s.payload->>'expires_at')::timestamptz>ts
      and s.payload->>'location_id'=own.payload->>'location_id'
      and greatest((s.payload->>'starts_at')::timestamptz,(own.payload->>'starts_at')::timestamptz,ts)<least((s.payload->>'ends_at')::timestamptz,(own.payload->>'ends_at')::timestamptz)
      and exists(select 1 from unnest(array['pizza','meal','drink','bakery','snack']) cat where (s.payload->>'category'=cat or s.payload->'acceptable_categories' ? cat)
        and (own.payload->>'category'=cat or own.payload->'acceptable_categories' ? cat))
      order by s.created_at desc,s.id limit 20
    loop
      for d in select null::uuid id union all select s.id from public.core_stealth_signals s join public.core_stealth_agents g on g.owner_id=s.owner_id
        where s.owner_id<>actor and s.owner_id<>(select owner_id from public.core_stealth_signals where id=c.id) and s.id>c.id and s.status='active'
        and s.payload->>'location_id'=own.payload->>'location_id' and core_private.stealth_awake(g.payload,ts)
        and (s.payload->>'expires_at')::timestamptz>ts
        order by id limit 21
      loop
        candidate:=core_private.stealth_candidate(case when d.id is null then array[own.id,c.id] else array[own.id,c.id,d.id] end,ts);
        if candidate is null then continue; end if;
        select array_agg(value::uuid) into owners from jsonb_array_elements_text(candidate->'owners');
        if exists(select 1 from public.core_stealth_agents g where g.owner_id=any(owners) and
          (select count(*) from public.core_stealth_proposals q where g.owner_id=any(q.owners) and (q.created_at at time zone (g.payload->>'timezone'))::date=(ts at time zone (g.payload->>'timezone'))::date)>=(g.payload->>'daily_invite_limit')::integer) then continue; end if;
        select array_agg(value::uuid) into ids from jsonb_array_elements_text(candidate->'signals');
        select array_agg(value::integer) into versions from jsonb_array_elements_text(candidate->'signal_versions');
        select array_agg(value::integer) into policies from jsonb_array_elements_text(candidate->'policy_versions');
        insert into public.core_stealth_proposals(fingerprint,owners,signals,signal_versions,policy_versions,payload)
          values(candidate->>'fingerprint',owners,ids,versions,policies,candidate->'payload') on conflict(fingerprint) do nothing;
        get diagnostics added=row_count; created:=created+added;
        if created>=maximum then exit; end if;
      end loop;
      exit when created>=maximum;
    end loop;
    exit when created>=maximum;
  end loop;
  return jsonb_build_object('created',created,'is_llm',false);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range or invalid_datetime_format or datetime_field_overflow or character_not_in_repertoire or not_null_violation or check_violation then
  raise exception using errcode='PT422',message='invalid_stealth_input';
end; $$;
revoke all on function core_private.stealth_validate(text,jsonb),core_private.stealth_awake(jsonb,timestamptz),core_private.stealth_candidate(uuid[],timestamptz),core_private.stealth_view(public.core_stealth_proposals,uuid),core_private.stealth_cloud(text,jsonb) from public,anon,authenticated;
grant execute on function core_private.stealth_cloud(text,jsonb) to authenticated;
create function public.core_stealth(action text,args jsonb default '{}') returns jsonb
language sql security invoker set search_path='' as $$ select core_private.stealth_cloud(action,args); $$;
revoke all on function public.core_stealth(text,jsonb) from public,anon,authenticated;
grant execute on function public.core_stealth(text,jsonb) to authenticated;
