-- Isolated synthetic account; all writes roll back. No mail, real users or messages.
begin;
create temporary table lifetime_fixture(uid uuid,sid uuid);
insert into lifetime_fixture values(gen_random_uuid(),gen_random_uuid());
insert into auth.users(id,email,email_confirmed_at,raw_app_meta_data)
select uid,uid::text||'@example.test',now(),jsonb_build_object('stealth_test_scope','lifetime-'||uid::text) from lifetime_fixture;
insert into auth.sessions(id,user_id) select sid,uid from lifetime_fixture;
grant select on lifetime_fixture to authenticated;
set local role authenticated;
do $test$
declare f record; v jsonb; first jsonb; second jsonb; replay jsonb; ts timestamptz:=clock_timestamp(); key uuid:=gen_random_uuid(); rejected boolean:=false;
begin
  select * into f from lifetime_fixture;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',f.uid,'session_id',f.sid,'role','authenticated')::text,true);
  perform public.core_dispatch('me');
  perform public.core_stealth('agent_put',jsonb_build_object('expected_version',0,'value',jsonb_build_object('name','Synthetic lifetime test','enabled',true,'include_demo',false,'daily_invite_limit',10,'timezone','Asia/Taipei','quiet_start_hour',null,'quiet_end_hour',null,'exploration','balanced','tone','warm','avatar_key','cat')));
  v:=jsonb_build_object('kind','need','category','meal','acceptable_categories','[]'::jsonb,'title','Synthetic dorm meal','location_id','自強10舍','starts_at',ts,'ends_at',ts+interval '1 hour','expires_at',ts+interval '24 hours','budget_max_twd',200,'known_cost_twd',null,'tags','[]'::jsonb,'roles','[]'::jsonb,'dietary_requirements','[]'::jsonb,'dietary_verified','[]'::jsonb,'participation_preference','receive');
  first:=public.core_stealth('signal_create',jsonb_build_object('value',v,'request_key',key));
  assert (first->>'expires_at')::timestamptz=(first->>'created_at')::timestamptz+interval '24 hours','lifetime anchored to server creation';
  assert (first->>'expires_at')::timestamptz>(first->>'ends_at')::timestamptz,'availability independent from lifetime';
  assert first->>'participation_preference'='receive' and (first->>'budget_max_twd')::int=200,'terms retained';
  replay:=public.core_stealth('signal_create',jsonb_build_object('value',jsonb_set(v,'{expires_at}',to_jsonb(ts+interval '23 hours')),'request_key',key));
  assert replay->>'id'=first->>'id' and replay->>'expires_at'=first->>'expires_at','retry does not extend lifetime';
  second:=public.core_stealth('signal_create',jsonb_build_object('value',jsonb_set(v,'{title}','"Synthetic second need"'::jsonb),'request_key',gen_random_uuid()));
  assert second->>'id'<>first->>'id','multiple requests coexist';
  begin
    perform public.core_stealth('signal_create',jsonb_build_object('value',jsonb_set(v,'{budget_max_twd}','300'::jsonb),'request_key',key));
  exception when sqlstate 'PT409' then rejected:=true; end;
  assert rejected,'changed confirmed budget cannot reuse key';
  perform public.core_stealth('revoke',jsonb_build_object('signal_id',first->>'id'));
  assert exists(select 1 from jsonb_array_elements(public.core_stealth('signals')->'items') item where item->>'id'=second->>'id' and item->>'status'='active'),'closing one leaves other active';
end;
$test$;
reset role;
rollback;
select '24h lifetime, multiple requests, retry and independent revocation passed' as result;
