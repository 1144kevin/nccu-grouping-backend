-- Rollback-only synthetic accounts. Never commit this script's fixtures.
create temporary table discovery_fixture(n int,uid uuid,sid uuid,signal_id uuid);
insert into discovery_fixture select n,gen_random_uuid(),gen_random_uuid(),gen_random_uuid() from generate_series(1,25) n;
insert into auth.users(id,email,email_confirmed_at,raw_app_meta_data)
  select uid,'discovery-rollback-'||uid||'@example.test',now(),jsonb_build_object('stealth_test_scope','discovery-rollback-only') from discovery_fixture;
insert into auth.sessions(id,user_id) select sid,uid from discovery_fixture;
insert into public.core_profiles(id,display_name) select uid,'隔離語義測試' from discovery_fixture on conflict do nothing;
insert into public.core_stealth_agents(owner_id,payload)
  select uid,jsonb_build_object('enabled',true,'include_demo',false,'name','測試','daily_invite_limit',10,
    'timezone','Asia/Taipei','quiet_start_hour',null,'quiet_end_hour',null,'exploration','balanced','tone','warm','avatar_key','cat') from discovery_fixture;
insert into public.core_stealth_signals(id,owner_id,request_key,payload,created_at)
  select signal_id,uid,sid,jsonb_build_object('kind',case when n=1 then 'need' else 'offer' end,
    'category','meal','acceptable_categories','[]'::jsonb,'title',case when n=24 then '舊的熱湯資源' else '私人測試原文' end,
    'location_id','main_gate','starts_at',now()+interval '90 minutes','ends_at',now()+interval '180 minutes',
    'expires_at',now()+interval '180 minutes','budget_max_twd',100,'known_cost_twd',80,'tags','[]'::jsonb,
    'roles',case when n=1 then '["share"]'::jsonb else '["order","pickup"]'::jsonb end,
    'dietary_requirements','[]'::jsonb,'dietary_verified','[]'::jsonb),case when n=24 then now()-interval '1 day' else now() end from discovery_fixture;
do $test$
declare f record; own record; target record; v jsonb; args jsonb; result jsonb; proposal public.core_stealth_proposals;
  terms jsonb; formed uuid;
begin
  select * into own from discovery_fixture where n=1;
  select * into target from discovery_fixture where n=24;
  -- Index only through each synthetic owner's live session, never an admin-key API.
  for f in select * from discovery_fixture where n<>25 loop
    perform set_config('request.jwt.claims',jsonb_build_object('sub',f.uid,'session_id',f.sid,'role','authenticated')::text,true);
    select jsonb_agg(case when (f.n=24 and i=1) or (f.n<>24 and i=2) then 1 else 0 end order by i) into v from generate_series(1,512) i;
    perform public.core_semantic_discovery('index',jsonb_build_object('model_id','synthetic-v1', 'items',jsonb_build_array(
      jsonb_build_object('signal_id',f.signal_id,'version',1,'vector',v))));
  end loop;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',own.uid,'session_id',own.sid,'role','authenticated')::text,true);
  select jsonb_agg(case when i=1 then 1 else 0 end order by i) into v from generate_series(1,512) i;
  args:=jsonb_build_object('model_id','synthetic-v1','max_results',3,'queries',jsonb_build_array(jsonb_build_object('signal_id',own.signal_id,'version',1,'vector',v)));
  begin
    perform public.core_semantic_discovery('index',jsonb_build_object('model_id','synthetic-v1','items',jsonb_build_array(jsonb_build_object('signal_id',target.signal_id,'version',1,'vector',v))));
    raise exception 'foreign index allowed';
  exception when sqlstate 'PT409' then null; end;
  assert target.signal_id not in (select id from public.core_stealth_signals where owner_id<>own.uid order by created_at desc,id limit 20),'target must be beyond old pool';
  result:=public.core_semantic_discovery('search',args);
  assert result->>'created'='1','cosine retrieval must discover old matching signal';
  select * into proposal from public.core_stealth_proposals where target.signal_id=any(signals) and own.uid=any(owners);
  assert proposal.id is not null and proposal.decisions='{}','discovery must not grant consent';
  assert proposal.terms is null and proposal.terms_revision=0,'discovery must not invent terms';
  begin
    terms:=jsonb_build_object('content','語義搜尋隔離成團：費用與分工由本人核對',
      'cost_per_person_twd',80,'deadline_at',now()+interval '60 minutes',
      'order_member',array_position(proposal.owners,target.uid),'pickup_member',array_position(proposal.owners,target.uid));
    result:=public.core_stealth_formation('terms',jsonb_build_object('proposal_id',proposal.id,'expected_revision',0,'value',terms));
    result:=public.core_stealth_formation('consent',jsonb_build_object('proposal_id',proposal.id,'expected_revision',1,'agree',true));
    assert result->>'formed_post_id' is null and result->>'confirmed_count'='1','single consent formed';
    terms:=terms||jsonb_build_object('content','語義搜尋隔離成團：修改方案後需要重新同意');
    result:=public.core_stealth_formation('terms',jsonb_build_object('proposal_id',proposal.id,'expected_revision',1,'value',terms));
    assert result->>'confirmed_count'='0' and result->>'terms_revision'='2','revision did not reset consent';
    perform public.core_stealth_formation('consent',jsonb_build_object('proposal_id',proposal.id,'expected_revision',2,'agree',true));
    perform set_config('request.jwt.claims',jsonb_build_object('sub',target.uid,'session_id',target.sid,'role','authenticated')::text,true);
    result:=public.core_stealth_formation('consent',jsonb_build_object('proposal_id',proposal.id,'expected_revision',2,'agree',true));
    formed:=(result->>'formed_post_id')::uuid;
    assert formed is not null,'unanimous semantic proposal failed formation';
    result:=public.core_stealth_formation('consent',jsonb_build_object('proposal_id',proposal.id,'expected_revision',2,'agree',true));
    assert (result->>'formed_post_id')::uuid=formed,'formation retry duplicated';
    perform public.core_chat('send',jsonb_build_object('post_id',formed,'content','隔離語義成團聊天室測試','client_key',gen_random_uuid()));
    result:=public.core_chat('messages',jsonb_build_object('post_id',formed));
    assert jsonb_array_length(result->'messages')=1,'formed chat unavailable';
    raise exception using errcode='ZX001',message='rollback formation scenario only';
  exception when sqlstate 'ZX001' then null; end;
  assert not exists(select 1 from public.core_stealth_proposals where (select signal_id from discovery_fixture where n=25)=any(signals)),'unindexed signal discovered';
  update public.core_stealth_agents set last_run_at=null where owner_id=own.uid;
  result:=public.core_semantic_discovery('search',args||'{"model_id":"different-model"}'::jsonb);
  assert result->>'created'='0','embedding spaces mixed';
  -- Revoke invalidates vector in the same transaction.
  update public.core_stealth_signals set status='revoked',version=version+1 where id=target.signal_id;
  assert not exists(select 1 from public.core_signal_vectors where signal_id=target.signal_id),'stale vector retained';
  -- A blocked, expired or incompatible candidate can never bypass the shared authority.
  update public.core_stealth_signals set status='active' where id=target.signal_id;
  insert into public.core_user_blocks(owner_id,blocked_id) values(own.uid,target.uid);
  assert core_private.stealth_candidate(array[own.signal_id,target.signal_id],now()) is null,'blocked candidate accepted';
  delete from public.core_user_blocks where owner_id=own.uid;
  update public.core_stealth_signals set payload=jsonb_set(payload,'{known_cost_twd}','1000') where id=target.signal_id;
  assert core_private.stealth_candidate(array[own.signal_id,target.signal_id],now()) is null,'over budget accepted';
  perform set_config('request.jwt.claims','{}',true);
  begin
    perform public.core_semantic_discovery('search',args);
    raise exception 'anonymous discovery allowed';
  exception when sqlstate 'PT401' then null; end;
end $test$;
select 'semantic discovery rollback smoke passed' result;
