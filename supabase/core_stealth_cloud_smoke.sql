-- Transaction-only accounts: no passwords, email, or persistent test records.
begin;
create temporary table stealth_fixture(n integer,uid uuid,sid uuid,signal_id uuid);
insert into stealth_fixture(n,uid,sid) select n,gen_random_uuid(),gen_random_uuid() from generate_series(1,4) n;
insert into auth.users(id,email,email_confirmed_at) select uid,uid::text||'@example.test',now() from stealth_fixture;
insert into auth.sessions(id,user_id) select sid,uid from stealth_fixture;
grant select,update on stealth_fixture to authenticated;
set local role authenticated;
do $test$
declare f record; first_user record; second_user record; third_user record; stranger record; a jsonb; v jsonb; s jsonb; replay jsonb; m jsonb; matches jsonb; own_count integer; p_id text;
begin
  select * into first_user from stealth_fixture where n=1;
  select * into second_user from stealth_fixture where n=2;
  select * into third_user from stealth_fixture where n=3;
  select * into stranger from stealth_fixture where n=4;
  for f in select * from stealth_fixture loop
    perform set_config('request.jwt.claims',jsonb_build_object('sub',f.uid,'session_id',f.sid,'role','authenticated')::text,true);
    perform public.core_dispatch('me');
    assert public.core_stealth('agent_get')='null'::jsonb,'new account has no agent';
    a:=jsonb_build_object('name','測試分身','enabled',true,'include_demo',false,'daily_invite_limit',10,'timezone','Asia/Taipei','quiet_start_hour',null,'quiet_end_hour',null,'exploration','balanced','tone','warm','avatar_key','cat');
    perform public.core_stealth('agent_put',jsonb_build_object('value',a,'expected_version',0));
    begin
      perform public.core_stealth('agent_put',jsonb_build_object('value',a,'expected_version',0));
      raise exception 'stale agent update allowed';
    exception when sqlstate 'PT409' then null; end;
    if f.n=4 then continue; end if;
    v:=jsonb_build_object('kind',case f.n when 1 then 'need' when 2 then 'offer' else 'plan' end,'category','pizza','acceptable_categories','[]'::jsonb,
      'title',case f.n when 1 then '私人的披薩需求原文' when 2 then '我的私人優惠券內容' else '我有私人行程' end,'location_id','main_gate',
      'starts_at',now()+interval '90 minutes','ends_at',now()+interval '180 minutes','expires_at',now()+interval '180 minutes',
      'budget_max_twd',120,'known_cost_twd',90,'tags',jsonb_build_array('晚餐'),'dietary_requirements','[]'::jsonb,'dietary_verified','[]'::jsonb,
      'roles',case f.n when 1 then jsonb_build_array('share') when 2 then jsonb_build_array('coupon','order') else jsonb_build_array('pickup') end);
    s:=public.core_stealth('signal_create',jsonb_build_object('value',v,'request_key',f.sid));
    replay:=public.core_stealth('signal_create',jsonb_build_object('value',v,'request_key',f.sid));
    assert s->>'id'=replay->>'id','idempotency';
    update stealth_fixture set signal_id=(s->>'id')::uuid where n=f.n;
    assert jsonb_array_length(public.core_stealth('signals')->'items')=1,'signals belong only to self';
    begin
      perform public.core_stealth('signal_create',jsonb_build_object('value',v||jsonb_build_object('owner_id',first_user.uid),'request_key',gen_random_uuid()));
      raise exception 'spoofed owner allowed';
    exception when sqlstate 'PT422' then null; end;
  end loop;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',first_user.uid,'session_id',first_user.sid,'role','authenticated')::text,true);
  m:=public.core_stealth('run',jsonb_build_object('max_results',5));
  assert (m->>'created')::integer>=2,'pair and triple matches';
  begin
    perform public.core_stealth('run');
    raise exception 'cooldown bypass allowed';
  exception when sqlstate 'PT429' then null; end;
  matches:=public.core_stealth('matches');
  assert matches::text not like '%我的私人優惠券內容%' and matches::text not like '%'||second_user.uid::text||'%','no other identity or private text';
  assert exists(select 1 from jsonb_array_elements(matches->'items') x where (x->>'member_count')::integer=3),'triple resource complementarity';
  select value->>'id' into p_id from jsonb_array_elements(matches->'items') where (value->>'member_count')::integer=2 and value->'reasons' ? '有人願意提供資源' limit 1;
  assert p_id is not null,'need + offer proposal';
  m:=public.core_stealth('decision',jsonb_build_object('proposal_id',p_id,'decision','accepted'));
  assert m->>'my_decision'='accepted' and m->>'status'='pending','acceptance is personal';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',second_user.uid,'session_id',second_user.sid,'role','authenticated')::text,true);
  m:=public.core_stealth('decision',jsonb_build_object('proposal_id',p_id,'decision','accepted'));
  assert m->>'status'='ready','all participants interested';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',stranger.uid,'session_id',stranger.sid,'role','authenticated')::text,true);
  assert jsonb_array_length(public.core_stealth('matches')->'items')=0,'stranger cannot list proposals';
  begin
    perform public.core_stealth('decision',jsonb_build_object('proposal_id',p_id,'decision','accepted'));
    raise exception 'stranger decided a proposal';
  exception when sqlstate 'PT404' then null; end;
  begin
    perform public.core_stealth('revoke',jsonb_build_object('signal_id',(select signal_id from stealth_fixture where n=1)));
    raise exception 'stranger revoked signal';
  exception when sqlstate 'PT404' then null; end;
  begin
    perform 1 from public.core_stealth_signals;
    raise exception 'direct table read allowed';
  exception when insufficient_privilege then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',first_user.uid,'session_id',first_user.sid,'role','authenticated')::text,true);
  perform public.core_stealth('revoke',jsonb_build_object('signal_id',(select signal_id from stealth_fixture where n=1)));
  assert not exists(select 1 from jsonb_array_elements(public.core_stealth('matches')->'items') x where x->>'status'<>'unavailable'),'revocation invalidates matches';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',first_user.uid,'session_id',gen_random_uuid(),'role','authenticated')::text,true);
  begin
    perform public.core_stealth('signals');
    raise exception 'revoked session read allowed';
  exception when sqlstate 'PT401' then null; end;
end; $test$;
reset role;
do $constraints$
declare ids uuid[]; base jsonb; candidate jsonb; demo uuid; first_id uuid; second_id uuid; current_hour integer;
begin
  select array_agg(signal_id order by n) filter(where n<=2) into ids from stealth_fixture;
  first_id:=ids[1]; second_id:=ids[2];
  update public.core_stealth_signals set status='active' where id=first_id;
  candidate:=core_private.stealth_candidate(ids,clock_timestamp());
  assert candidate is not null,'valid baseline';
  select payload into base from public.core_stealth_signals where id=second_id;
  update public.core_stealth_signals set payload=payload||jsonb_build_object('known_cost_twd',151) where id=second_id;
  assert core_private.stealth_candidate(ids,clock_timestamp()) is null,'conflicting cost or over budget rejected';
  update public.core_stealth_signals set payload=base||jsonb_build_object('starts_at',now()+interval '5 hours','ends_at',now()+interval '6 hours','expires_at',now()+interval '6 hours') where id=second_id;
  assert core_private.stealth_candidate(ids,clock_timestamp()) is null,'non-overlapping times rejected';
  update public.core_stealth_signals set payload=base||jsonb_build_object('location_id','south_gate') where id=second_id;
  assert core_private.stealth_candidate(ids,clock_timestamp()) is null,'different locations rejected';
  update public.core_stealth_signals set payload=base where id=second_id;
  update public.core_stealth_signals set payload=payload||jsonb_build_object('dietary_requirements',jsonb_build_array('素食')) where id=first_id;
  assert core_private.stealth_candidate(ids,clock_timestamp()) is null,'unknown dietary claims rejected';
  update public.core_stealth_signals set payload=payload||jsonb_build_object('dietary_requirements','[]'::jsonb) where id=first_id;
  current_hour:=extract(hour from clock_timestamp() at time zone 'Asia/Taipei')::integer;
  update public.core_stealth_agents set payload=payload||jsonb_build_object('quiet_start_hour',current_hour,'quiet_end_hour',(current_hour+1)%24) where owner_id=(select uid from stealth_fixture where n=1);
  assert core_private.stealth_candidate(ids,clock_timestamp()) is null,'quiet hours respected';
  update public.core_stealth_agents set payload=payload||jsonb_build_object('quiet_start_hour',null,'quiet_end_hour',null) where owner_id=(select uid from stealth_fixture where n=1);
  update auth.users set raw_app_meta_data=coalesce(raw_app_meta_data,'{}')||jsonb_build_object('stealth_test_scope','isolated') where id=(select uid from stealth_fixture where n=2);
  assert core_private.stealth_candidate(ids,clock_timestamp()) is null,'synthetic fixture cannot invite real account';
  update auth.users set raw_app_meta_data=raw_app_meta_data-'stealth_test_scope' where id=(select uid from stealth_fixture where n=2);
  select s.id into demo from public.core_stealth_signals s join auth.users u on u.id=s.owner_id where u.raw_app_meta_data->>'demo_batch'='showcase_20261017_v1' and s.payload->>'kind'='offer' limit 1;
  if demo is not null then
    update public.core_stealth_signals set payload=payload||jsonb_build_object('category','meal') where id=first_id;
    assert core_private.stealth_candidate(array[first_id,demo],clock_timestamp()) is null,'demo requires explicit opt in';
    update public.core_stealth_agents set payload=payload||jsonb_build_object('include_demo',true) where owner_id=(select uid from stealth_fixture where n=1);
    candidate:=core_private.stealth_candidate(array[first_id,demo],clock_timestamp());
    assert candidate is not null and (candidate->'payload'->>'is_demo')::boolean,'demo clearly labeled after opt in';
    update public.core_stealth_signals set payload=jsonb_set(payload,'{expires_at}',to_jsonb(now()-interval '1 second')) where id=demo;
    perform core_private.stealth_refresh_demo(now());
    assert (select (payload->>'expires_at')::timestamptz>now() from public.core_stealth_signals where id=demo),'expired inert demo resource refreshed';
  end if;
end; $constraints$;
rollback;
