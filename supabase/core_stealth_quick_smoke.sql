-- All identities and messages roll back. Does not send mail or touch existing users.
begin;
create temporary table quick_fixture(n int,uid uuid,sid uuid,signal_id uuid,proposal_id uuid);
insert into quick_fixture(n,uid,sid) select n,gen_random_uuid(),gen_random_uuid() from generate_series(1,4) n;
insert into auth.users(id,email,email_confirmed_at,raw_app_meta_data)
 select uid,uid::text||'@example.test',now(),jsonb_build_object('stealth_test_scope','quick-smoke-'||(select uid::text from quick_fixture where n=4)) from quick_fixture;
insert into auth.sessions(id,user_id) select sid,uid from quick_fixture;
grant select,update on quick_fixture to authenticated;
set local role authenticated;
do $test$
declare f record; a record; b record; outsider record; v jsonb; p text; m jsonb; x jsonb; msg jsonb; k uuid:=gen_random_uuid();
begin
 select * into a from quick_fixture where n=1; select * into b from quick_fixture where n=2; select * into outsider from quick_fixture where n=4;
 for f in select * from quick_fixture loop
  perform set_config('request.jwt.claims',jsonb_build_object('sub',f.uid,'session_id',f.sid,'role','authenticated')::text,true);
  perform public.core_dispatch('me');
  perform public.core_stealth('agent_put',jsonb_build_object('expected_version',0,'value',jsonb_build_object('name','隔離快速分身','enabled',true,'include_demo',false,'daily_invite_limit',10,'timezone','Asia/Taipei','quiet_start_hour',null,'quiet_end_hour',null,'exploration','balanced','tone','warm','avatar_key','cat')));
  if f.n=4 then continue; end if;
  v:=jsonb_build_object('kind','need','category','drink','acceptable_categories','[]'::jsonb,'title','我今天想喝50嵐','location_id','main_gate','starts_at',now(),'ends_at',now()+interval '2 hours','expires_at',now()+interval '2 hours','budget_max_twd',null,'known_cost_twd',null,'tags',jsonb_build_array(case when f.n=3 then '品牌:清心' else '品牌:50嵐' end),'roles','[]'::jsonb,'dietary_requirements','[]'::jsonb,'dietary_verified','[]'::jsonb);
  x:=public.core_stealth('signal_create',jsonb_build_object('value',v,'request_key',f.sid));
  update quick_fixture set signal_id=(x->>'id')::uuid where n=f.n;
  assert public.core_stealth_quick('lookup',jsonb_build_object('request_key',f.sid))->>'id'=x->>'id','own retry lookup';
 end loop;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a.uid,'session_id',a.sid,'role','authenticated')::text,true);
 assert public.core_stealth_quick('lookup',jsonb_build_object('request_key',b.sid))='null'::jsonb,'no foreign lookup';
 m:=public.core_stealth('run',jsonb_build_object('max_results',5));
 assert (m->>'created')::int=1,'different explicit brands cannot match, even under balanced exploration';
 select value->>'id' into p from jsonb_array_elements(public.core_stealth('matches')->'items') limit 1;
 update quick_fixture set proposal_id=p::uuid;
 assert public.core_stealth_quick('state',jsonb_build_object('proposal_id',p))->>'opened'='false','no room before consent';
 perform public.core_stealth('decision',jsonb_build_object('proposal_id',p,'decision','accepted'));
 assert public.core_stealth_quick('state',jsonb_build_object('proposal_id',p))->>'opened'='false','one consent insufficient';
 perform set_config('request.jwt.claims',jsonb_build_object('sub',outsider.uid,'session_id',outsider.sid,'role','authenticated')::text,true);
 begin perform public.core_stealth_quick('state',jsonb_build_object('proposal_id',p)); raise exception 'outsider read allowed'; exception when sqlstate 'PT404' then null; end;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',b.uid,'session_id',b.sid,'role','authenticated')::text,true);
 perform public.core_stealth('decision',jsonb_build_object('proposal_id',p,'decision','accepted'));
 x:=public.core_stealth_quick('state',jsonb_build_object('proposal_id',p));
 assert x->>'opened'='true' and x->>'can_send'='true','unknown price and roles do not block preliminary chat';
 msg:=public.core_stealth_quick('send',jsonb_build_object('proposal_id',p,'content','我想喝無糖，可以討論誰去拿嗎？','request_key',k));
 assert public.core_stealth_quick('send',jsonb_build_object('proposal_id',p,'content','我想喝無糖，可以討論誰去拿嗎？','request_key',k))->>'id'=msg->>'id','same retry no duplicate';
 begin perform public.core_stealth_quick('send',jsonb_build_object('proposal_id',p,'content','changed','request_key',k)); raise exception 'idempotency mismatch allowed'; exception when sqlstate 'PT409' then null; end;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a.uid,'session_id',a.sid,'role','authenticated')::text,true);
 x:=public.core_stealth_quick('state',jsonb_build_object('proposal_id',p));
 assert jsonb_array_length(x->'items')=1 and x->'items'->0->>'is_me'='false','participant reads other member message';
 assert x::text not like '%'||b.uid::text||'%' and x::text not like '%example.test%','no email or account id disclosure';
 assert not exists(select 1 from jsonb_array_elements(public.core_stealth('matches')->'items') q(value) where q.value->>'status'='formed'),'chat does not create formal order';
 perform public.core_stealth('decision',jsonb_build_object('proposal_id',p,'decision','declined'));
 assert public.core_stealth_quick('state',jsonb_build_object('proposal_id',p))->>'can_send'='false','withdrawal makes room readonly';
 begin perform public.core_stealth_quick('send',jsonb_build_object('proposal_id',p,'content','blocked after withdrawal','request_key',gen_random_uuid())); raise exception 'withdrawal ignored'; exception when sqlstate 'PT409' then null; end;
end $test$;
reset role;
update public.core_stealth_proposals p set payload=jsonb_set(payload,'{expires_at}',to_jsonb(now()-interval '1 minute')),
 decisions=(select jsonb_object_agg(u::text,'accepted') from unnest(p.owners) u)
 where p.id=(select proposal_id from quick_fixture limit 1);
set local role authenticated;
do $test$
declare f record; v jsonb; begin
 select * into f from quick_fixture where n=1;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',f.uid,'session_id',f.sid,'role','authenticated')::text,true);
 v:=public.core_stealth_quick('state',jsonb_build_object('proposal_id',f.proposal_id));
 assert v->>'opened'='true' and v->>'can_send'='true','opened accepted room survives invitation expiry';
 assert exists(select 1 from jsonb_array_elements(public.core_stealth('matches')->'items') q(value) where q.value->>'coordination_opened'='true'),'opened room remains discoverable';
 perform public.core_stealth_quick('send',jsonb_build_object('proposal_id',f.proposal_id,'content','Continue after invitation expiry','request_key',gen_random_uuid()));
end $test$;
reset role;
update public.core_stealth_signals set status='revoked' where id=(select signal_id from quick_fixture where n=1);
set local role authenticated;
do $$ declare f record; begin
 select * into f from quick_fixture where n=1;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',f.uid,'session_id',f.sid,'role','authenticated')::text,true);
 assert public.core_stealth_quick('state',jsonb_build_object('proposal_id',f.proposal_id))->>'can_send'='false','revoked demand still stops an expired opened room';
 begin perform public.core_stealth_quick('send',jsonb_build_object('proposal_id',f.proposal_id,'content','must not send','request_key',gen_random_uuid())); raise exception 'revocation ignored'; exception when sqlstate 'PT409' then null; end;
end $$;
reset role;
update public.core_stealth_signals set status='active' where id=(select signal_id from quick_fixture where n=1);
insert into public.core_user_blocks(owner_id,blocked_id) select a.uid,b.uid from quick_fixture a,quick_fixture b where a.n=1 and b.n=2;
set local role authenticated;
do $test$
declare f record; begin
 select * into f from quick_fixture where n=1;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',f.uid,'session_id',f.sid,'role','authenticated')::text,true);
 begin perform public.core_stealth_quick('state',jsonb_build_object('proposal_id',f.proposal_id)); raise exception 'block ignored'; exception when sqlstate 'PT404' then null; end;
 begin perform 1 from public.core_stealth_room_messages; raise exception 'direct table read allowed'; exception when insufficient_privilege then null; end;
end $test$;
reset role;
rollback;
select 'quick stealth: consent, private chat, retries, withdrawal, brands and isolation passed';
