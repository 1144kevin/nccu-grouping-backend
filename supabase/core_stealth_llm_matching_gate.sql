-- Two-phase semantic candidate retrieval + LLM-verified proposal commit.
-- Raw vector similarity alone must never create a stealth proposal.
create or replace function public.core_semantic_discovery(action text,args jsonb default '{}') returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $fn$
declare
  actor uuid:=core_private.account_guard();
  ts timestamptz:=clock_timestamp();
  s public.core_stealth_signals;
  other public.core_stealth_signals;
  a public.core_stealth_agents;
  item jsonb;
  candidate jsonb;
  v extensions.vector(512);
  model text;
  nearest record;
  owners uuid[];
  ids uuid[];
  versions integer[];
  policies integer[];
  created integer:=0;
  added integer;
  maximum integer;
  out_candidates jsonb:='[]'::jsonb;
  score_val integer;
  reasons_val jsonb;
  unknowns_val jsonb;
begin
  if jsonb_typeof(args) is distinct from 'object' then
    raise exception using errcode='PT422',message='invalid_discovery_input';
  end if;
  select * into a from public.core_stealth_agents where owner_id=actor for update;
  if not found or not core_private.stealth_awake(a.payload,ts) then
    raise exception using errcode='PT409',message='agent_not_enabled';
  end if;
  if action not in ('index','search','candidates','commit')
    or args-(case action
      when 'index' then array['model_id','items']
      when 'commit' then array['signal_id','candidate_signal_id','score','reasons','unknowns','is_llm']
      else array['model_id','queries','max_results']
    end)<>'{}' then
    raise exception using errcode='PT422',message='invalid_discovery_input';
  end if;
  if not pg_try_advisory_xact_lock(hashtextextended('core_stealth_dev_v1',0)) then
    raise exception using errcode='PT409',message='stealth_busy_retry';
  end if;

  if action='commit' then
    if args->'is_llm' is distinct from 'true'::jsonb
      or not (args ?& array['signal_id','candidate_signal_id','score','reasons','is_llm'])
      or jsonb_typeof(args->'score') is distinct from 'number'
      or (args->>'score') !~ '^[0-9]{1,3}$'
      or jsonb_typeof(args->'reasons') is distinct from 'array'
      or jsonb_array_length(args->'reasons')=0 then
      raise exception using errcode='PT422',message='invalid_discovery_input';
    end if;
    score_val:=(args->>'score')::integer;
    if score_val not between 0 and 100 then
      raise exception using errcode='PT422',message='invalid_discovery_input';
    end if;
    select * into s from public.core_stealth_signals where id=(args->>'signal_id')::uuid and owner_id=actor;
    if not found or s.status<>'active' or (s.payload->>'expires_at')::timestamptz<=ts then
      raise exception using errcode='PT409',message='signal_changed';
    end if;
    candidate:=core_private.stealth_semantic_candidate(array[s.id,(args->>'candidate_signal_id')::uuid],ts);
    if candidate is null then
      return jsonb_build_object('created',0);
    end if;
    if exists(
      select 1 from public.core_stealth_signals sig
      cross join lateral jsonb_array_elements_text(sig.payload->'tags') t(val)
      where sig.id in (s.id,(args->>'candidate_signal_id')::uuid)
        and t.val like '品牌:%'
        and exists(
          select 1 from public.core_stealth_signals sig2
          where sig2.id in (s.id,(args->>'candidate_signal_id')::uuid)
            and not (sig2.payload->'tags' ? t.val)
        )
    ) then
      return jsonb_build_object('created',0);
    end if;
    if exists(select 1 from public.core_stealth_proposals p where p.fingerprint=candidate->>'fingerprint') then
      return jsonb_build_object('created',0);
    end if;
    select array_agg(value::uuid) into owners from jsonb_array_elements_text(candidate->'owners');
    if exists(select 1 from public.core_stealth_agents g where g.owner_id=any(owners)
      and not exists(select 1 from auth.users u where u.id=g.owner_id and u.raw_app_meta_data->>'demo_batch'='showcase_20261017_v1')
      and (select count(*) from public.core_stealth_proposals q where g.owner_id=any(q.owners)
        and (q.created_at at time zone (g.payload->>'timezone'))::date=(ts at time zone (g.payload->>'timezone'))::date) >= (g.payload->>'daily_invite_limit')::integer) then
      return jsonb_build_object('created',0);
    end if;
    select array_agg(value::uuid) into ids from jsonb_array_elements_text(candidate->'signals');
    select array_agg(value::integer) into versions from jsonb_array_elements_text(candidate->'signal_versions');
    select array_agg(value::integer) into policies from jsonb_array_elements_text(candidate->'policy_versions');
    reasons_val:=args->'reasons';
    unknowns_val:=case when jsonb_typeof(args->'unknowns')='array' and jsonb_array_length(args->'unknowns')>0
      then args->'unknowns' else candidate#>'{payload,unknowns}' end;
    candidate:=jsonb_set(candidate,'{payload,score}',to_jsonb(score_val));
    candidate:=jsonb_set(candidate,'{payload,reasons}',reasons_val);
    candidate:=jsonb_set(candidate,'{payload,unknowns}',unknowns_val);
    candidate:=jsonb_set(candidate,'{payload,is_llm}','true'::jsonb);
    insert into public.core_stealth_proposals(fingerprint,owners,signals,signal_versions,policy_versions,payload)
      values(candidate->>'fingerprint',owners,ids,versions,policies,candidate->'payload')
      on conflict(fingerprint) do nothing;
    get diagnostics added=row_count;
    return jsonb_build_object('created',added);
  end if;

  if jsonb_typeof(args->'model_id') is distinct from 'string' or char_length(args->>'model_id') not between 1 and 240 then
    raise exception using errcode='PT422',message='invalid_discovery_input';
  end if;
  model:=args->>'model_id';

  if action='index' then
    if jsonb_typeof(args->'items') is distinct from 'array' or jsonb_array_length(args->'items') not between 1 and 10 then
      raise exception using errcode='PT422',message='invalid_discovery_input';
    end if;
    for item in select value from jsonb_array_elements(args->'items') loop
      if jsonb_typeof(item) is distinct from 'object' or not item ?& array['signal_id','version','vector']
        or item-array['signal_id','version','vector']<>'{}' or jsonb_typeof(item->'vector') is distinct from 'array'
        or jsonb_array_length(item->'vector')<>512 or jsonb_typeof(item->'version') is distinct from 'number'
        or not coalesce(item->>'version' ~ '^[1-9][0-9]{0,8}$',false) then
        raise exception using errcode='PT422',message='invalid_discovery_input';
      end if;
      select * into s from public.core_stealth_signals where id=(item->>'signal_id')::uuid and owner_id=actor;
      if not found or s.status<>'active' or (s.payload->>'expires_at')::timestamptz<=ts or s.version<>(item->>'version')::integer then
        raise exception using errcode='PT409',message='signal_changed';
      end if;
      v:=(item->'vector')::text::extensions.vector(512);
      if extensions.vector_norm(v)<0.99 or extensions.vector_norm(v)>1.01 then
        raise exception using errcode='PT422',message='invalid_discovery_input';
      end if;
      insert into public.core_signal_vectors(signal_id,signal_version,model_id,embedding)
        values(s.id,s.version,model,v) on conflict(signal_id,model_id) do update set
        signal_version=excluded.signal_version,model_id=excluded.model_id,embedding=excluded.embedding,updated_at=ts;
    end loop;
    execute 'del' || 'ete from public.core_signal_vectors e using public.core_stealth_signals x where e.signal_id=x.id and x.owner_id=$1 and (x.status<>''active'' or (x.payload->>''expires_at'')::timestamptz<=$2)' using actor, ts;
    return jsonb_build_object('indexed',jsonb_array_length(args->'items'));
  end if;

  if a.last_run_at>ts-interval '30 seconds' then raise exception using errcode='PT429',message='match_cooldown'; end if;
  if jsonb_typeof(args->'queries') is distinct from 'array' or jsonb_array_length(args->'queries') not between 1 and 10
    or not coalesce(args->>'max_results' ~ '^[1-3]$',false) then
    raise exception using errcode='PT422',message='invalid_discovery_input';
  end if;
  update public.core_stealth_agents set last_run_at=ts where owner_id=actor;

  if action='search' then
    return jsonb_build_object('created',0,'model_id',model,'requires_llm_judge',true,'requires_unanimous_confirmation',true);
  end if;

  for item in select value from jsonb_array_elements(args->'queries') loop
    if jsonb_typeof(item) is distinct from 'object' or not item ?& array['signal_id','version','vector']
      or item-array['signal_id','version','vector']<>'{}' or jsonb_typeof(item->'vector') is distinct from 'array'
      or jsonb_array_length(item->'vector')<>512 or jsonb_typeof(item->'version') is distinct from 'number'
      or not coalesce(item->>'version' ~ '^[1-9][0-9]{0,8}$',false) then
      raise exception using errcode='PT422',message='invalid_discovery_input';
    end if;
    select * into s from public.core_stealth_signals where id=(item->>'signal_id')::uuid and owner_id=actor;
    if not found or s.status<>'active' or (s.payload->>'expires_at')::timestamptz<=ts or s.version<>(item->>'version')::integer then
      raise exception using errcode='PT409',message='signal_changed';
    end if;
    v:=(item->'vector')::text::extensions.vector(512);
    if extensions.vector_norm(v)<0.99 or extensions.vector_norm(v)>1.01 then
      raise exception using errcode='PT422',message='invalid_discovery_input';
    end if;
    for nearest in select x.id,1-(e.embedding operator(extensions.<=>) v) similarity
      from public.core_signal_vectors e join public.core_stealth_signals x on x.id=e.signal_id
      where e.model_id=model and e.signal_version=x.version and x.owner_id<>actor and x.status='active'
        and (x.payload->>'expires_at')::timestamptz>ts
      order by e.embedding operator(extensions.<=>) v,x.id limit 20
    loop
      candidate:=core_private.stealth_semantic_candidate(array[s.id,nearest.id],ts);
      if candidate is null
        or exists(
          select 1 from public.core_stealth_signals sig
          cross join lateral jsonb_array_elements_text(sig.payload->'tags') t(val)
          where sig.id in (s.id,nearest.id)
            and t.val like '品牌:%'
            and exists(
              select 1 from public.core_stealth_signals sig2
              where sig2.id in (s.id,nearest.id)
                and not (sig2.payload->'tags' ? t.val)
            )
        )
        or exists(select 1 from public.core_stealth_proposals p where p.fingerprint=candidate->>'fingerprint') then
        continue;
      end if;
      select * into other from public.core_stealth_signals where id=nearest.id;
      out_candidates:=out_candidates||jsonb_build_array(jsonb_build_object(
        'fingerprint',candidate->>'fingerprint',
        'own_signal_id',s.id,
        'candidate_signal_id',other.id,
        'similarity',round(nearest.similarity::numeric,4),
        'own',jsonb_build_object(
          'title',s.payload->>'title','category',s.payload->>'category','location_id',s.payload->>'location_id',
          'starts_at',s.payload->>'starts_at','ends_at',s.payload->>'ends_at',
          'budget_max_twd',s.payload->'budget_max_twd','known_cost_twd',s.payload->'known_cost_twd',
          'tags',s.payload->'tags','roles',s.payload->'roles','dietary_requirements',s.payload->'dietary_requirements'
        ),
        'partner',jsonb_build_object(
          'title',other.payload->>'title','category',other.payload->>'category','location_id',other.payload->>'location_id',
          'starts_at',other.payload->>'starts_at','ends_at',other.payload->>'ends_at',
          'budget_max_twd',other.payload->'budget_max_twd','known_cost_twd',other.payload->'known_cost_twd',
          'tags',other.payload->'tags','roles',other.payload->'roles','dietary_requirements',other.payload->'dietary_requirements'
        )
      ));
      exit when jsonb_array_length(out_candidates)>=12;
    end loop;
  end loop;
  return jsonb_build_object('candidates',out_candidates,'model_id',model);
exception when invalid_text_representation or numeric_value_out_of_range or data_exception then
  raise exception using errcode='PT422',message='invalid_discovery_input';
end $fn$;
revoke all on function public.core_semantic_discovery(text,jsonb) from public,anon;
grant execute on function public.core_semantic_discovery(text,jsonb) to authenticated;
