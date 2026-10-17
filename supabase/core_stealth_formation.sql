-- Additive final consent and atomic formation; existing core dispatcher/chat stay intact.
alter table public.core_stealth_proposals
  add column terms jsonb,
  add column terms_revision integer not null default 0 check(terms_revision between 0 and 20),
  add column terms_author_id uuid references public.core_profiles(id),
  add column terms_acceptances jsonb not null default '{}',
  add column formed_post_id uuid unique references public.core_posts(id),
  add column formed_at timestamptz;
alter table public.core_stealth_signals drop constraint core_stealth_signals_status_check;
alter table public.core_stealth_signals add constraint core_stealth_signals_status_check check(status in ('active','revoked','consumed'));
create index core_stealth_terms_author on public.core_stealth_proposals(terms_author_id) where terms_author_id is not null;

create or replace function core_private.stealth_view(p public.core_stealth_proposals,actor uuid) returns jsonb
language plpgsql stable set search_path='' as $$
declare valid boolean:=true; i integer; own_signal jsonb; own_agent jsonb; decision text; state text; accepted integer;
begin
  if actor is null or not actor=any(p.owners) then return null; end if;
  if (p.payload->>'expires_at')::timestamptz<=clock_timestamp() then valid:=false; end if;
  for i in 1..cardinality(p.owners) loop
    if not exists(select 1 from public.core_stealth_signals s join public.core_stealth_agents a on a.owner_id=s.owner_id
      where s.id=p.signals[i] and s.owner_id=p.owners[i] and s.status='active' and s.version=p.signal_versions[i]
      and (s.payload->>'expires_at')::timestamptz>clock_timestamp() and (a.payload->>'enabled')::boolean and a.policy_version=p.policy_versions[i]) then valid:=false; end if;
  end loop;
  decision:=coalesce(p.decisions->>actor::text,'pending');
  state:=case when p.formed_post_id is not null then 'formed'
    when not valid or exists(select 1 from jsonb_each_text(p.decisions) where value='declined') then 'unavailable'
    when (select count(*) from jsonb_each_text(p.decisions) where value='accepted')=cardinality(p.owners) then 'ready' else 'pending' end;
  select count(*) into accepted from unnest(p.owners) u where p.terms_revision>0 and p.terms_acceptances->>u::text=p.terms_revision::text;
  select payload into own_signal from public.core_stealth_signals where id=p.signals[array_position(p.owners,actor)];
  select payload into own_agent from public.core_stealth_agents where owner_id=actor;
  return p.payload||jsonb_build_object('id',p.id,'created_at',p.created_at,'my_decision',decision,'status',state,'my_signal_title',own_signal->>'title','my_roles',own_signal->'roles',
    'invitation_text',case own_agent->>'tone' when 'playful' then '分身幫你找到一組可以一起揪的人！' when 'direct' then '找到符合條件的互補配對。' else '有一組時間與需求合得來的夥伴，想一起看看嗎？' end,
    'why_me',jsonb_build_array('你的需求類型、取餐地點與時間符合這個方案','只有全員同意同一版費用與分工，才會建立拼單'),'is_llm',false,
    'terms',p.terms,'terms_revision',p.terms_revision,'my_member_number',array_position(p.owners,actor),
    'host_member_number',array_position(p.owners,p.terms_author_id),'confirmed_count',accepted,
    'my_terms_accepted',p.terms_revision>0 and coalesce(p.terms_acceptances->>actor::text,'')=p.terms_revision::text,
    'formed_post_id',p.formed_post_id,'formed_at',p.formed_at);
end; $$;

create function core_private.stealth_formation(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=auth.uid(); p public.core_stealth_proposals; v jsonb; view jsonb; allowed text[];
  ts timestamptz:=clock_timestamp(); revision integer; deadline timestamptz; cost integer; n integer; i integer;
  post_payload jsonb; post_id uuid; order_name text; pickup_name text; place text;
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false)) or
    not exists(select 1 from auth.sessions where user_id=actor and id=(auth.jwt()->>'session_id')::uuid and (not_after is null or not_after>ts)) then
    raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  allowed:=case action when 'terms' then array['proposal_id','expected_revision','value'] when 'consent' then array['proposal_id','expected_revision','agree'] else null end;
  if allowed is null or jsonb_typeof(args) is distinct from 'object' or args-allowed<>'{}' or
    jsonb_typeof(args->'expected_revision') is distinct from 'number' or args->>'expected_revision' !~ '^[0-9]{1,2}$' then
    raise exception using errcode='PT422',message='invalid_formation_input'; end if;
  if not pg_try_advisory_xact_lock(hashtextextended('core_stealth_dev_v1',0)) then raise exception using errcode='PT409',message='stealth_busy_retry'; end if;
  select * into p from public.core_stealth_proposals q where q.id=(args->>'proposal_id')::uuid and actor=any(q.owners) for update;
  if not found then raise exception using errcode='PT404',message='stealth_not_found'; end if;
  revision:=(args->>'expected_revision')::integer;
  if revision<>p.terms_revision then raise exception using errcode='PT409',message='terms_revision_conflict'; end if;
  if action='consent' and args->'agree' is distinct from 'true'::jsonb then raise exception using errcode='PT422',message='explicit_consent_required'; end if;
  -- A response lost after the final commit can be retried without duplicate posts/members.
  if p.formed_post_id is not null then
    if action='consent' then return core_private.stealth_view(p,actor); end if;
    raise exception using errcode='PT409',message='proposal_already_formed'; end if;
  view:=core_private.stealth_view(p,actor);
  if view->>'status'='unavailable' then raise exception using errcode='PT409',message='proposal_unavailable'; end if;
  if coalesce((p.payload->>'is_demo')::boolean,false) then raise exception using errcode='PT409',message='demo_cannot_form'; end if;
  n:=cardinality(p.owners);
  if action='terms' then
    v:=args->'value';
    if jsonb_typeof(v) is distinct from 'object' or v-array['content','cost_per_person_twd','deadline_at','order_member','pickup_member']<>'{}' or
      jsonb_typeof(v->'content') is distinct from 'string' or char_length(btrim(v->>'content')) not between 10 and 300 or
      jsonb_typeof(v->'cost_per_person_twd') is distinct from 'number' or v->>'cost_per_person_twd' !~ '^[0-9]{1,6}$' or
      jsonb_typeof(v->'deadline_at') is distinct from 'string' or v->>'deadline_at' !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' or
      jsonb_typeof(v->'order_member') is distinct from 'number' or v->>'order_member' !~ '^[1-3]$' or
      jsonb_typeof(v->'pickup_member') is distinct from 'number' or v->>'pickup_member' !~ '^[1-3]$' then
      raise exception using errcode='PT422',message='invalid_formation_input'; end if;
    cost:=(v->>'cost_per_person_twd')::integer; deadline:=(v->>'deadline_at')::timestamptz;
    if cost>100000 or (p.payload->>'budget_max_twd' is not null and cost>(p.payload->>'budget_max_twd')::integer) or
      (p.payload->>'known_cost_twd' is not null and cost<>(p.payload->>'known_cost_twd')::integer) then raise exception using errcode='PT422',message='formation_cost_conflict'; end if;
    if (v->>'order_member')::integer>n or (v->>'pickup_member')::integer>n or deadline<=ts or deadline>(p.payload->>'ends_at')::timestamptz then
      raise exception using errcode='PT422',message='invalid_formation_input'; end if;
    -- Exact replay preserves approvals. Any edit (including changing host) resets all approvals.
    if p.terms=v and p.terms_author_id=actor then return view; end if;
    if p.terms_revision>=20 then raise exception using errcode='PT409',message='terms_revision_limit'; end if;
    update public.core_stealth_proposals set terms=v,terms_revision=terms_revision+1,terms_author_id=actor,terms_acceptances='{}' where id=p.id returning * into p;
    return core_private.stealth_view(p,actor);
  end if;
  if p.terms is null or p.terms_revision=0 then raise exception using errcode='PT409',message='terms_required'; end if;
  deadline:=(p.terms->>'deadline_at')::timestamptz;
  if deadline<=ts then raise exception using errcode='PT409',message='formation_deadline_passed'; end if;
  update public.core_stealth_proposals set terms_acceptances=terms_acceptances||jsonb_build_object(actor::text,revision),
    decisions=decisions||jsonb_build_object(actor::text,'accepted') where id=p.id returning * into p;
  if exists(select 1 from unnest(p.owners) u where coalesce(p.terms_acceptances->>u::text,'')<>revision::text) then return core_private.stealth_view(p,actor); end if;
  -- Same transaction and feature lock as revoke/agent policy changes/other formations.
  perform 1 from public.core_stealth_signals where id=any(p.signals) order by id for update;
  if core_private.stealth_view(p,actor)->>'status'='unavailable' then raise exception using errcode='PT409',message='proposal_unavailable'; end if;
  select display_name into order_name from public.core_profiles where id=p.owners[(p.terms->>'order_member')::integer];
  select display_name into pickup_name from public.core_profiles where id=p.owners[(p.terms->>'pickup_member')::integer];
  place:=case p.payload->>'location_id' when 'main_gate' then '政大正門' when 'east_gate' then '政大東側門' else '政大南門' end;
  post_payload:=core_private.post_input(jsonb_build_object('content',btrim(p.terms->>'content')||E'\n下單：'||order_name||'；取餐：'||pickup_name||E'\n配合時段：'||(p.payload->>'starts_at')||' ～ '||(p.payload->>'ends_at'),
    'tags',jsonb_build_array('偷揪成團',p.payload->>'category'),'capacity',n,'deadline_at',deadline,'price_per_person',p.terms->'cost_per_person_twd',
    'location',jsonb_build_object('name',place,'latitude',24.9875,'longitude',121.576,'address',null,'source',null)));
  insert into public.core_posts(author_id,payload,deadline_at,capacity,request_key,original_payload)
    values(p.terms_author_id,post_payload,deadline,n,'stealth-formation-'||p.id::text,post_payload) returning id into post_id;
  insert into public.core_join_requests(post_id,requester_id,status,decided_at)
    select post_id,u,'accepted',ts from unnest(p.owners) u where u<>p.terms_author_id;
  update public.core_stealth_signals set status='consumed',version=version+1 where id=any(p.signals);
  update public.core_stealth_proposals set formed_post_id=post_id,formed_at=ts where id=p.id returning * into p;
  -- Existing chat lists derive rooms from the post + accepted members; no fake user message needed.
  return core_private.stealth_view(p,actor);
exception when invalid_text_representation or numeric_value_out_of_range or invalid_datetime_format or datetime_field_overflow or character_not_in_repertoire then
  raise exception using errcode='PT422',message='invalid_formation_input';
end; $$;
revoke all on function core_private.stealth_formation(text,jsonb) from public,anon,authenticated;
grant execute on function core_private.stealth_formation(text,jsonb) to authenticated;
create function public.core_stealth_formation(action text,args jsonb default '{}') returns jsonb
language sql security invoker set search_path='' as $$select core_private.stealth_formation(action,args);$$;
revoke all on function public.core_stealth_formation(text,jsonb) from public,anon,authenticated;
grant execute on function public.core_stealth_formation(text,jsonb) to authenticated;
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
    if exists(select 1 from public.core_stealth_signals where id=(args->>'signal_id')::uuid and owner_id=actor and status='consumed') then raise exception using errcode='PT409',message='signal_already_consumed'; end if;
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
    if p.formed_post_id is not null then raise exception using errcode='PT409',message='proposal_already_formed'; end if;
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
  if coalesce((a.payload->>'include_demo')::boolean,false) then perform core_private.stealth_refresh_demo(ts); end if;
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
        if exists(select 1 from public.core_stealth_agents g where g.owner_id=any(owners) and not exists(select 1 from auth.users u where u.id=g.owner_id and u.raw_app_meta_data->>'demo_batch'='showcase_20261017_v1') and
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
