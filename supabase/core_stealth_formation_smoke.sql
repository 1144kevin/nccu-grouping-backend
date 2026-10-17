-- All writes, including Auth fixtures, roll back. Uses real public RPCs as authenticated.
begin;
create temporary table formation_fixture(n integer,uid uuid,sid uuid,signal_id uuid);
insert into formation_fixture(n,uid,sid) select n,gen_random_uuid(),gen_random_uuid() from generate_series(1,4) n;
insert into auth.users(id,email,email_confirmed_at,raw_app_meta_data)
  select uid,uid::text||'@example.test',now(),jsonb_build_object('stealth_test_scope','formation-transaction') from formation_fixture;
insert into auth.sessions(id,user_id) select sid,uid from formation_fixture;
grant select,update on formation_fixture to authenticated;
set local role authenticated;
do $test$
declare f record; first_user record; stranger record; a jsonb; v jsonb; s jsonb; m jsonb; t jsonb; proposal text; other text; post text; result jsonb;
begin
  select * into first_user from formation_fixture where n=1;
  select * into stranger from formation_fixture where n=4;
  for f in select * from formation_fixture order by n loop
    perform set_config('request.jwt.claims',jsonb_build_object('sub',f.uid,'session_id',f.sid,'role','authenticated')::text,true);
    perform public.core_dispatch('me');
    a:=jsonb_build_object('name','測試分身','enabled',true,'include_demo',false,'daily_invite_limit',10,'timezone','Asia/Taipei','quiet_start_hour',null,'quiet_end_hour',null,'exploration','balanced','tone','warm','avatar_key','cat');
    perform public.core_stealth('agent_put',jsonb_build_object('value',a,'expected_version',0));
    if f.n=4 then continue; end if;
    v:=jsonb_build_object('kind',case f.n when 1 then 'need' when 2 then 'offer' else 'plan' end,'category','pizza','acceptable_categories','[]'::jsonb,
      'title','不可公開的私密需求 '||f.n,'location_id','main_gate','starts_at',now()+interval '90 minutes','ends_at',now()+interval '180 minutes','expires_at',now()+interval '180 minutes',
      'budget_max_twd',120,'known_cost_twd',90,'tags',jsonb_build_array('晚餐'),'dietary_requirements','[]'::jsonb,'dietary_verified','[]'::jsonb,
      'roles',case f.n when 1 then jsonb_build_array('share') when 2 then jsonb_build_array('coupon','order') else jsonb_build_array('pickup') end);
    s:=public.core_stealth('signal_create',jsonb_build_object('value',v,'request_key',f.sid));
    update formation_fixture set signal_id=(s->>'id')::uuid where n=f.n;
  end loop;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',first_user.uid,'session_id',first_user.sid,'role','authenticated')::text,true);
  perform public.core_stealth('run',jsonb_build_object('max_results',5));
  select value->>'id' into proposal from jsonb_array_elements(public.core_stealth('matches')->'items') where (value->>'member_count')::integer=3 limit 1;
  select value->>'id' into other from jsonb_array_elements(public.core_stealth('matches')->'items') where (value->>'member_count')::integer=2 limit 1;
  assert proposal is not null and other is not null,'triple plus overlapping pair';
  t:=jsonb_build_object('content','大家確認後一起分享披薩，費用含已協調項目。','cost_per_person_twd',90,'deadline_at',now()+interval '120 minutes','order_member',2,'pickup_member',3);
  begin
    perform public.core_stealth_formation('terms',jsonb_build_object('proposal_id',proposal,'expected_revision',0,'value',t||jsonb_build_object('cost_per_person_twd',100)));
    raise exception 'changed known price without explanation';
  exception when sqlstate 'PT422' then null; end;
  begin
    m:=public.core_stealth_formation('terms',jsonb_build_object('proposal_id',proposal,'expected_revision',0,'value',t||jsonb_build_object('cost_per_person_twd',100,'cost_change_note','補上每人十元已協調運費')));
    assert (m->'terms'->>'cost_per_person_twd')::integer=100 and m->'terms'->>'cost_change_note'='補上每人十元已協調運費','explained change within budget is reviewable';
    assert (m->>'confirmed_count')::integer=0,'changing quote does not imply any consent';
    perform public.core_stealth_formation('consent',jsonb_build_object('proposal_id',proposal,'expected_revision',1,'agree',true));
    m:=public.core_stealth_formation('terms',jsonb_build_object('proposal_id',proposal,'expected_revision',1,'value',t||jsonb_build_object('cost_per_person_twd',110,'cost_change_note','改為每人二十元已協調運費')));
    assert (m->>'terms_revision')::integer=2 and (m->>'confirmed_count')::integer=0,'revised costs require all new approvals';
    raise exception using errcode='ZX001',message='rollback explained cost scenario';
  exception when sqlstate 'ZX001' then null; end;
  -- Nested exception blocks roll back these negative scenarios, keeping the success fixture intact.
  begin
    perform public.core_stealth_formation('terms',jsonb_build_object('proposal_id',proposal,'expected_revision',0,'value',t));
    perform public.core_stealth_formation('consent',jsonb_build_object('proposal_id',proposal,'expected_revision',1,'agree',true));
    perform public.core_stealth('decision',jsonb_build_object('proposal_id',proposal,'decision','declined'));
    begin
      perform public.core_stealth_formation('consent',jsonb_build_object('proposal_id',proposal,'expected_revision',1,'agree',true));
      raise exception 'withdrawn consent formed';
    exception when sqlstate 'PT409' then null; end;
    raise exception using errcode='ZX001',message='rollback negative scenario';
  exception when sqlstate 'ZX001' then null; end;
  begin
    perform public.core_stealth_formation('terms',jsonb_build_object('proposal_id',proposal,'expected_revision',0,'value',t));
    perform public.core_stealth_formation('consent',jsonb_build_object('proposal_id',proposal,'expected_revision',1,'agree',true));
    perform public.core_stealth('agent_put',jsonb_build_object('value',a||jsonb_build_object('enabled',false),'expected_version',1));
    begin
      perform public.core_stealth_formation('consent',jsonb_build_object('proposal_id',proposal,'expected_revision',1,'agree',true));
      raise exception 'paused agent formed';
    exception when sqlstate 'PT409' then null; end;
    raise exception using errcode='ZX001',message='rollback negative scenario';
  exception when sqlstate 'ZX001' then null; end;
  begin
    perform public.core_stealth_formation('terms',jsonb_build_object('proposal_id',proposal,'expected_revision',0,'value',t));
    perform public.core_stealth('revoke',jsonb_build_object('signal_id',(select signal_id from formation_fixture where n=1)));
    begin
      perform public.core_stealth_formation('consent',jsonb_build_object('proposal_id',proposal,'expected_revision',1,'agree',true));
      raise exception 'revoked signal formed';
    exception when sqlstate 'PT409' then null; end;
    raise exception using errcode='ZX001',message='rollback negative scenario';
  exception when sqlstate 'ZX001' then null; end;
  begin
    perform public.core_stealth_formation('consent',jsonb_build_object('proposal_id',proposal,'expected_revision',0,'agree',true));
    raise exception 'missing terms accepted';
  exception when sqlstate 'PT409' then null; end;
  begin
    perform public.core_stealth_formation('terms',jsonb_build_object('proposal_id',proposal,'expected_revision',0,'value',t||jsonb_build_object('cost_per_person_twd',130)));
    raise exception 'overbudget terms accepted';
  exception when sqlstate 'PT422' then null; end;
  m:=public.core_stealth_formation('terms',jsonb_build_object('proposal_id',proposal,'expected_revision',0,'value',t));
  assert (m->>'terms_revision')::integer=1 and (m->>'confirmed_count')::integer=0,'proposal is not consent';
  assert jsonb_array_length(m->'planning_members')=3,'sanitized role context for all three members';
  assert not exists(select 1 from jsonb_array_elements(m->'planning_members') x where x-array['number','roles']<>'{}'),'planning context has no private prose or identities';
  assert m::text not like '%'||stranger.uid::text||'%' and m->>'formed_post_id' is null,'safe pre-formation view';
  m:=public.core_stealth_formation('consent',jsonb_build_object('proposal_id',proposal,'expected_revision',1,'agree',true));
  assert (m->>'confirmed_count')::integer=1 and m->>'formed_post_id' is null,'one approval cannot form';
  m:=public.core_stealth_formation('terms',jsonb_build_object('proposal_id',proposal,'expected_revision',1,'value',t));
  assert (m->>'terms_revision')::integer=1 and (m->>'confirmed_count')::integer=1,'exact replay preserves consent';
  t:=t||jsonb_build_object('content','大家確認新版後一起分享披薩，費用含已協調項目。');
  m:=public.core_stealth_formation('terms',jsonb_build_object('proposal_id',proposal,'expected_revision',1,'value',t));
  assert (m->>'terms_revision')::integer=2 and (m->>'confirmed_count')::integer=0,'edit resets everyone';
  begin
    perform public.core_stealth_formation('consent',jsonb_build_object('proposal_id',proposal,'expected_revision',1,'agree',true));
    raise exception 'stale revision accepted';
  exception when sqlstate 'PT409' then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',stranger.uid,'session_id',stranger.sid,'role','authenticated')::text,true);
  begin
    perform public.core_stealth_formation('consent',jsonb_build_object('proposal_id',proposal,'expected_revision',2,'agree',true));
    raise exception 'stranger accepted';
  exception when sqlstate 'PT404' then null; end;
  for f in select * from formation_fixture where n<=3 order by n loop
    perform set_config('request.jwt.claims',jsonb_build_object('sub',f.uid,'session_id',f.sid,'role','authenticated')::text,true);
    begin
      perform public.core_stealth_formation('consent',jsonb_build_object('proposal_id',proposal,'expected_revision',2,'agree',1));
      raise exception 'numeric implicit consent';
    exception when sqlstate 'PT422' then null; end;
    m:=public.core_stealth_formation('consent',jsonb_build_object('proposal_id',proposal,'expected_revision',2,'agree',true));
    if f.n<3 then assert m->>'formed_post_id' is null,'not unanimous'; end if;
  end loop;
  assert m->>'status'='formed' and (m->>'confirmed_count')::integer=3,'unanimous forms';
  assert m->'planning_members'='[]'::jsonb,'no new planning facts after formation';
  post:=m->>'formed_post_id';
  assert public.core_stealth_formation('consent',jsonb_build_object('proposal_id',proposal,'expected_revision',2,'agree',true))->>'formed_post_id'=post,'final retry is idempotent';
  for f in select * from formation_fixture where n<=3 order by n loop
    perform set_config('request.jwt.claims',jsonb_build_object('sub',f.uid,'session_id',f.sid,'role','authenticated')::text,true);
    result:=public.core_dispatch('post',jsonb_build_object('post_id',post));
    assert result->>'status'='full' and (result->>'joined')::integer=3 and result->>'price_per_person'='90.00','core post valid';
    assert result->>'content' not like '%不可公開%','raw private signals never copied';
    assert exists(select 1 from jsonb_array_elements(public.core_chat('list')->'items') x where x->'post'->>'id'=post),'chat room appears for every member';
    perform public.core_chat('messages',jsonb_build_object('post_id',post));
    assert (public.core_stealth('signals')->'items'->0->>'status')='consumed','signal consumed';
    begin
      perform public.core_stealth('revoke',jsonb_build_object('signal_id',f.signal_id));
      raise exception 'consumed signal revoked';
    exception when sqlstate 'PT409' then null; end;
  end loop;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',first_user.uid,'session_id',first_user.sid,'role','authenticated')::text,true);
  assert exists(select 1 from jsonb_array_elements(public.core_stealth('matches')->'items') x where x->>'id'=other and x->>'status'='unavailable'),'overlapping opportunities invalidated';
  begin
    perform public.core_stealth_formation('terms',jsonb_build_object('proposal_id',other,'expected_revision',0,'value',t||jsonb_build_object('pickup_member',1)));
    raise exception 'same signal formed twice';
  exception when sqlstate 'PT409' then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',stranger.uid,'session_id',stranger.sid,'role','authenticated')::text,true);
  begin
    perform public.core_chat('messages',jsonb_build_object('post_id',post));
    raise exception 'stranger accessed chat';
  exception when sqlstate 'PT403' then null; end;
  select * into f from formation_fixture where n=2;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',f.uid,'session_id',f.sid,'role','authenticated')::text,true);
  perform public.core_dispatch('withdraw',jsonb_build_object('post_id',post));
  assert (public.core_dispatch('post',jsonb_build_object('post_id',post))->>'joined')::integer=2,'existing withdrawal frees seat';
  begin
    perform public.core_chat('messages',jsonb_build_object('post_id',post));
    raise exception 'withdrawn member accessed chat';
  exception when sqlstate 'PT403' then null; end;
end; $test$;
reset role;
do $verify$
begin
  assert (select count(*) from public.core_posts where author_id in(select uid from formation_fixture))=1,'exactly one formed post';
end; $verify$;
rollback;
