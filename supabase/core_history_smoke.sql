begin;
create temporary table history_fixture(name text,user_id uuid,session_id uuid);
insert into history_fixture values('host',gen_random_uuid(),gen_random_uuid()),
  ('member',gen_random_uuid(),gen_random_uuid()),('stranger',gen_random_uuid(),gen_random_uuid());
insert into auth.users(id,email,email_confirmed_at)
  select user_id,user_id::text||'@example.test',now() from history_fixture;
insert into auth.sessions(id,user_id) select session_id,user_id from history_fixture;
grant select on history_fixture to authenticated;
set local role authenticated;
do $test$
declare h record; m record; s record; p jsonb; joined jsonb; result jsonb; value jsonb;
begin
  select * into h from history_fixture where name='host';
  select * into m from history_fixture where name='member';
  select * into s from history_fixture where name='stranger';
  assert not has_function_privilege('anon','public.core_order_history(jsonb)','EXECUTE'),'anonymous history denied';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  value:=jsonb_build_object('content','歷史流程測試：拒絕與退出後可以查閱。','tags',jsonb_build_array('歷史測試'),
    'capacity',3,'deadline_at',now()+interval '1 day','price_per_person','50.00',
    'location',jsonb_build_object('name','虛構測試點','latitude',24.987,'longitude',121.576));
  p:=public.core_dispatch('create',jsonb_build_object('value',value,'request_key','history-smoke-key'));
  perform set_config('request.jwt.claims',jsonb_build_object('sub',m.user_id,'session_id',m.session_id,'role','authenticated')::text,true);
  joined:=public.core_dispatch('join',jsonb_build_object('post_id',p->>'id','expected_version',1));
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  perform public.core_dispatch('decide',jsonb_build_object('join_id',joined->>'id','status','rejected'));
  assert jsonb_array_length(public.core_order_history()->'items')=0,'open host post is not history';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',m.user_id,'session_id',m.session_id,'role','authenticated')::text,true);
  result:=public.core_order_history();
  assert jsonb_array_length(result->'items')=1 and result->'items'->0->>'my_status'='rejected','rejection preserved';
  perform public.core_dispatch('join',jsonb_build_object('post_id',p->>'id','expected_version',1));
  assert jsonb_array_length(public.core_order_history()->'items')=0,'reapply leaves history';
  perform public.core_dispatch('withdraw',jsonb_build_object('post_id',p->>'id'));
  assert public.core_order_history()->'items'->0->>'my_status'='cancelled','withdrawal preserved';
  begin
    perform public.core_order_history('{"actor":"forged"}');
    raise exception 'actor injection incorrectly allowed';
  exception when sqlstate 'PT422' then null; end;
  begin
    perform public.core_order_history('{"cursor":"%%%"}');
    raise exception 'malformed cursor incorrectly allowed';
  exception when sqlstate 'PT422' then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',s.user_id,'session_id',s.session_id,'role','authenticated')::text,true);
  assert jsonb_array_length(public.core_order_history()->'items')=0,'stranger sees no personal history';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  perform public.core_dispatch('cancel',jsonb_build_object('post_id',p->>'id'));
  assert public.core_order_history()->'items'->0->>'status'='cancelled','closed host post preserved';
end;
$test$;
rollback;
