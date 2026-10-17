-- Admin-only fixture scopes prevent live synthetic tests from inviting real users.
create or replace function core_private.stealth_candidate(ids uuid[],ts timestamptz) returns jsonb
language plpgsql set search_path='' as $$
declare n integer:=cardinality(ids); owners uuid[]; versions integer[]; policies integer[]; cats text[]; chosen text; roles text[]; tags text[]; reqs text[];
  st timestamptz; en timestamptz; ex timestamptz; budget integer; cost integer; kinds integer; offers integer; plans integer; reasons jsonb; unknowns jsonb:='[]'; fp text;
begin
  if exists(select 1 from public.core_stealth_signals s join auth.users u on u.id=s.owner_id where s.id=any(ids) and u.raw_app_meta_data->>'demo_batch'='showcase_20261017_v1') and exists(select 1 from public.core_stealth_signals s join public.core_stealth_agents a on a.owner_id=s.owner_id where s.id=any(ids) and not coalesce((a.payload->>'include_demo')::boolean,false)) then return null; end if;
  if (select count(distinct coalesce(u.raw_app_meta_data->>'stealth_test_scope','')) from public.core_stealth_signals s join auth.users u on u.id=s.owner_id where s.id=any(ids) and u.raw_app_meta_data->>'demo_batch' is distinct from 'showcase_20261017_v1')>1 then return null; end if;
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
    'reasons',reasons,'unknowns',unknowns,'is_demo',exists(select 1 from auth.users u where u.id=any(owners) and u.raw_app_meta_data->>'demo_batch'='showcase_20261017_v1')));
end; $$;

-- Rank the bounded pool before applying quotas; retries preserve decisions.
create or replace function core_private.stealth_cloud(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=auth.uid(); allowed text[]; v jsonb; a public.core_stealth_agents; s public.core_stealth_signals; p public.core_stealth_proposals;
  previous public.core_stealth_agents; ts timestamptz:=clock_timestamp(); own public.core_stealth_signals; c record; d record;
  candidate jsonb; result jsonb; created integer:=0; added integer; maximum integer; owners uuid[]; ids uuid[]; versions integer[]; policies integer[]; policy_changed boolean; pool jsonb:='[]';
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
    policy_changed:=a.owner_id is null or a.payload->'enabled'<>v->'enabled' or a.payload->'exploration'<>v->'exploration' or coalesce(a.payload->'include_demo','false'::jsonb)<>v->'include_demo';
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
      (select * from public.core_stealth_proposals q where actor=any(q.owners) order by created_at desc,id desc limit 50) x;
    return jsonb_build_object('items',result);
  elsif action='decision' then
    if args->>'decision' is null or args->>'decision' not in ('accepted','declined') then raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
    select * into p from public.core_stealth_proposals q where q.id=(args->>'proposal_id')::uuid and actor=any(q.owners);
    if not found then raise exception using errcode='PT404',message='stealth_not_found'; end if;
    result:=core_private.stealth_view(p,actor);
    if coalesce(p.decisions->>actor::text,'pending')=args->>'decision' then return result; end if;
    if result->>'status'='unavailable' then raise exception using errcode='PT409',message='proposal_unavailable'; end if;
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
    for c in select source.id from public.core_stealth_signals source join public.core_stealth_agents g on g.owner_id=source.owner_id where source.owner_id<>actor and source.status='active' and (coalesce((a.payload->>'include_demo')::boolean,false) or not exists(select 1 from auth.users u where u.id=source.owner_id and u.raw_app_meta_data->>'demo_batch'='showcase_20261017_v1'))
      and exists(select 1 from auth.users u where u.id=source.owner_id and (coalesce(u.raw_app_meta_data->>'stealth_test_scope','')=coalesce((select raw_app_meta_data->>'stealth_test_scope' from auth.users where id=actor),'') or u.raw_app_meta_data->>'demo_batch'='showcase_20261017_v1')) and core_private.stealth_awake(g.payload,ts) and (source.payload->>'expires_at')::timestamptz>ts
      and source.payload->>'location_id'=own.payload->>'location_id'
      and greatest((source.payload->>'starts_at')::timestamptz,(own.payload->>'starts_at')::timestamptz,ts)<least((source.payload->>'ends_at')::timestamptz,(own.payload->>'ends_at')::timestamptz)
      and exists(select 1 from unnest(array['pizza','meal','drink','bakery','snack']) cat where (source.payload->>'category'=cat or source.payload->'acceptable_categories' ? cat)
        and (own.payload->>'category'=cat or own.payload->'acceptable_categories' ? cat))
      order by source.created_at desc,source.id limit 20
    loop
      for d in select null::uuid id union all select source.id from public.core_stealth_signals source join public.core_stealth_agents g on g.owner_id=source.owner_id
        where source.owner_id<>actor and source.owner_id<>(select owner_id from public.core_stealth_signals where id=c.id) and source.id>c.id and source.status='active'
        and exists(select 1 from auth.users u where u.id=source.owner_id and (coalesce(u.raw_app_meta_data->>'stealth_test_scope','')=coalesce((select raw_app_meta_data->>'stealth_test_scope' from auth.users where id=actor),'') or u.raw_app_meta_data->>'demo_batch'='showcase_20261017_v1')) and source.payload->>'location_id'=own.payload->>'location_id' and core_private.stealth_awake(g.payload,ts)
        and (source.payload->>'expires_at')::timestamptz>ts
        order by id limit 21
      loop
        candidate:=core_private.stealth_candidate(case when d.id is null then array[own.id,c.id] else array[own.id,c.id,d.id] end,ts);
        if candidate is null then continue; end if;
        pool:=pool||jsonb_build_array(candidate);
      end loop;
    end loop;
  end loop;
  for candidate in select value from (select distinct on (value->>'fingerprint') value from jsonb_array_elements(pool)) ranked order by (value->'payload'->>'score')::integer desc,value->>'fingerprint' loop
        select array_agg(value::uuid) into owners from jsonb_array_elements_text(candidate->'owners');
        if exists(select 1 from public.core_stealth_agents g where g.owner_id=any(owners) and
          (select count(*) from public.core_stealth_proposals q where g.owner_id=any(q.owners) and (q.created_at at time zone (g.payload->>'timezone'))::date=(ts at time zone (g.payload->>'timezone'))::date)>=(g.payload->>'daily_invite_limit')::integer) then continue; end if;
        select array_agg(value::uuid) into ids from jsonb_array_elements_text(candidate->'signals');
        select array_agg(value::integer) into versions from jsonb_array_elements_text(candidate->'signal_versions');
        select array_agg(value::integer) into policies from jsonb_array_elements_text(candidate->'policy_versions');
        insert into public.core_stealth_proposals(fingerprint,owners,signals,signal_versions,policy_versions,payload)
          values(candidate->>'fingerprint',owners,ids,versions,policies,candidate->'payload') on conflict(fingerprint) do nothing;
        get diagnostics added=row_count; created:=created+added;
        exit when created>=maximum;
  end loop;
  return jsonb_build_object('created',created,'is_llm',false);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range or invalid_datetime_format or datetime_field_overflow or character_not_in_repertoire or not_null_violation or check_violation then
  raise exception using errcode='PT422',message='invalid_stealth_input';
end; $$;
