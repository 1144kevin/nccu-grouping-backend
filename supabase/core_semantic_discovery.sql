-- Local embeddings, private cloud vector retrieval. No paid inference or raw-text export.
create extension if not exists vector with schema extensions;
create table public.core_signal_vectors (
  signal_id uuid primary key references public.core_stealth_signals(id) on delete cascade,
  signal_version integer not null,
  model_id text not null check(char_length(model_id) between 1 and 240),
  embedding extensions.vector(512) not null,
  updated_at timestamptz not null default clock_timestamp()
);
alter table public.core_signal_vectors enable row level security;
revoke all on public.core_signal_vectors from public,anon,authenticated;
-- Exact cosine scan initially: no ANN filtering/recall surprises on the small dev dataset.
create index core_signal_vectors_model on public.core_signal_vectors(model_id);
create function core_private.invalidate_signal_vector() returns trigger
language plpgsql security definer set search_path='' as $$
begin
  if new.version<>old.version or new.payload<>old.payload or new.status<>old.status then
    delete from public.core_signal_vectors where signal_id=new.id;
  end if;
  return new;
end; $$;
revoke all on function core_private.invalidate_signal_vector() from public,anon,authenticated;
create trigger core_signal_vector_invalidation after update on public.core_stealth_signals
  for each row execute function core_private.invalidate_signal_vector();

create function public.core_semantic_discovery(action text,args jsonb default '{}') returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=core_private.account_guard(); ts timestamptz:=clock_timestamp();
  s public.core_stealth_signals; a public.core_stealth_agents; item jsonb; candidate jsonb;
  v extensions.vector(512); model text; nearest record; owners uuid[]; ids uuid[];
  versions integer[]; policies integer[]; created integer:=0; added integer; maximum integer;
begin
  if jsonb_typeof(args) is distinct from 'object' then
    raise exception using errcode='PT422',message='invalid_discovery_input'; end if;
  select * into a from public.core_stealth_agents where owner_id=actor for update;
  if not found or not core_private.stealth_awake(a.payload,ts) then
    raise exception using errcode='PT409',message='agent_not_enabled'; end if;
  if action not in ('index','search') or args-(case action when 'index' then array['model_id','items'] else array['model_id','queries','max_results'] end)<>'{}'
    or jsonb_typeof(args->'model_id') is distinct from 'string' or char_length(args->>'model_id') not between 1 and 240 then
    raise exception using errcode='PT422',message='invalid_discovery_input'; end if;
  model:=args->>'model_id';
  if not pg_try_advisory_xact_lock(hashtextextended('core_stealth_dev_v1',0)) then
    raise exception using errcode='PT409',message='stealth_busy_retry'; end if;
  if action='index' then
    if jsonb_typeof(args->'items') is distinct from 'array' or jsonb_array_length(args->'items') not between 1 and 10 then
      raise exception using errcode='PT422',message='invalid_discovery_input'; end if;
    for item in select value from jsonb_array_elements(args->'items') loop
      if jsonb_typeof(item) is distinct from 'object' or not item ?& array['signal_id','version','vector']
        or item-array['signal_id','version','vector']<>'{}' or jsonb_typeof(item->'vector') is distinct from 'array'
        or jsonb_array_length(item->'vector')<>512 or jsonb_typeof(item->'version') is distinct from 'number'
        or not coalesce(item->>'version' ~ '^[1-9][0-9]{0,8}$',false) then
        raise exception using errcode='PT422',message='invalid_discovery_input'; end if;
      select * into s from public.core_stealth_signals where id=(item->>'signal_id')::uuid and owner_id=actor;
      if not found or s.status<>'active' or (s.payload->>'expires_at')::timestamptz<=ts or s.version<>(item->>'version')::integer then
        raise exception using errcode='PT409',message='signal_changed'; end if;
      v:=(item->'vector')::text::extensions.vector(512);
      if extensions.vector_norm(v)<0.99 or extensions.vector_norm(v)>1.01 then
        raise exception using errcode='PT422',message='invalid_discovery_input'; end if;
      insert into public.core_signal_vectors(signal_id,signal_version,model_id,embedding)
        values(s.id,s.version,model,v) on conflict(signal_id) do update set
        signal_version=excluded.signal_version,model_id=excluded.model_id,embedding=excluded.embedding,updated_at=ts;
    end loop;
    delete from public.core_signal_vectors e using public.core_stealth_signals x where e.signal_id=x.id
      and x.owner_id=actor and (x.status<>'active' or (x.payload->>'expires_at')::timestamptz<=ts);
    return jsonb_build_object('indexed',jsonb_array_length(args->'items'));
  end if;
  if a.last_run_at>ts-interval '30 seconds' then raise exception using errcode='PT429',message='match_cooldown'; end if;
  if jsonb_typeof(args->'queries') is distinct from 'array' or jsonb_array_length(args->'queries') not between 1 and 10
    or not coalesce(args->>'max_results' ~ '^[1-3]$',false) then
    raise exception using errcode='PT422',message='invalid_discovery_input'; end if;
  maximum:=(args->>'max_results')::integer;
  update public.core_stealth_agents set last_run_at=ts where owner_id=actor;
  for item in select value from jsonb_array_elements(args->'queries') loop
    if jsonb_typeof(item) is distinct from 'object' or not item ?& array['signal_id','version','vector']
      or item-array['signal_id','version','vector']<>'{}' or jsonb_typeof(item->'vector') is distinct from 'array'
      or jsonb_array_length(item->'vector')<>512 or jsonb_typeof(item->'version') is distinct from 'number'
      or not coalesce(item->>'version' ~ '^[1-9][0-9]{0,8}$',false) then
      raise exception using errcode='PT422',message='invalid_discovery_input'; end if;
    select * into s from public.core_stealth_signals where id=(item->>'signal_id')::uuid and owner_id=actor;
    if not found or s.status<>'active' or (s.payload->>'expires_at')::timestamptz<=ts or s.version<>(item->>'version')::integer then
      raise exception using errcode='PT409',message='signal_changed'; end if;
    v:=(item->'vector')::text::extensions.vector(512);
    if extensions.vector_norm(v)<0.99 or extensions.vector_norm(v)>1.01 then
      raise exception using errcode='PT422',message='invalid_discovery_input'; end if;
    -- Search beyond the legacy newest-20 pool. Only eligible pairs reach proposal creation.
    for nearest in select x.id,1-(e.embedding operator(extensions.<=>) v) similarity
      from public.core_signal_vectors e join public.core_stealth_signals x on x.id=e.signal_id
      where e.model_id=model and e.signal_version=x.version and x.owner_id<>actor and x.status='active'
        and (x.payload->>'expires_at')::timestamptz>ts
        and x.payload->>'location_id'=s.payload->>'location_id'
        and core_private.stealth_candidate(array[s.id,x.id],ts) is not null
      order by e.embedding operator(extensions.<=>) v,x.id limit 100
    loop
      if nearest.similarity<0.2 then continue; end if;
      candidate:=core_private.stealth_candidate(array[s.id,nearest.id],clock_timestamp());
      if candidate is null or exists(select 1 from public.core_stealth_proposals p where p.fingerprint=candidate->>'fingerprint') then continue; end if;
      select array_agg(value::uuid) into owners from jsonb_array_elements_text(candidate->'owners');
      if exists(select 1 from public.core_stealth_agents g where g.owner_id=any(owners) and
        (select count(*) from public.core_stealth_proposals p where g.owner_id=any(p.owners)
          and (p.created_at at time zone (g.payload->>'timezone'))::date=(ts at time zone (g.payload->>'timezone'))::date)>=(g.payload->>'daily_invite_limit')::integer) then continue; end if;
      select array_agg(value::uuid) into ids from jsonb_array_elements_text(candidate->'signals');
      select array_agg(value::integer) into versions from jsonb_array_elements_text(candidate->'signal_versions');
      select array_agg(value::integer) into policies from jsonb_array_elements_text(candidate->'policy_versions');
      candidate:=jsonb_set(candidate,'{payload,reasons}',(candidate#>'{payload,reasons}')||jsonb_build_array('透過需求向量搜尋找到，仍須本人確認'));
      insert into public.core_stealth_proposals(fingerprint,owners,signals,signal_versions,policy_versions,payload)
        values(candidate->>'fingerprint',owners,ids,versions,policies,candidate->'payload') on conflict(fingerprint) do nothing;
      get diagnostics added=row_count; created:=created+added;
      exit when created>=maximum;
    end loop;
    exit when created>=maximum;
  end loop;
  return jsonb_build_object('created',created,'model_id',model,'requires_unanimous_confirmation',true);
exception when invalid_text_representation or numeric_value_out_of_range or data_exception then
  raise exception using errcode='PT422',message='invalid_discovery_input';
end; $$;
revoke all on function public.core_semantic_discovery(text,jsonb) from public,anon;
grant execute on function public.core_semantic_discovery(text,jsonb) to authenticated;
