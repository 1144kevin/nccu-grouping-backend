begin;
create temporary table order_activity_fixture(name text,user_id uuid,session_id uuid);
insert into order_activity_fixture values('host',gen_random_uuid(),gen_random_uuid()),
  ('member',gen_random_uuid(),gen_random_uuid()),('stranger',gen_random_uuid(),gen_random_uuid());
insert into auth.users(id,email,email_confirmed_at,raw_user_meta_data)
  select user_id,user_id::text||'@example.test',now(),jsonb_build_object('display_name',name) from order_activity_fixture;
insert into auth.sessions(id,user_id) select session_id,user_id from order_activity_fixture;
grant select on order_activity_fixture to authenticated;
set local role authenticated;
do $test$
declare h record; m record; s record; p jsonb; joined jsonb; result jsonb;
begin
  select * into h from order_activity_fixture where name='host';
  select * into m from order_activity_fixture where name='member';
  select * into s from order_activity_fixture where name='stranger';
  assert not has_function_privilege('anon','public.core_order_activity(jsonb)','EXECUTE'),'anonymous activity denied';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  p:=public.core_dispatch('create',jsonb_build_object('value',jsonb_build_object(
    'content','申請動態測試：主揪與本人可查看，其他人不能讀取。','tags',jsonb_build_array('動態測試'),
    'capacity',3,'deadline_at',now()+interval '1 day','price_per_person','50.00',
    'location',jsonb_build_object('name','虛構測試點','latitude',24.987,'longitude',121.576)), 'request_key','order-activity-post'));
  perform set_config('request.jwt.claims',jsonb_build_object('sub',m.user_id,'session_id',m.session_id,'role','authenticated')::text,true);
  joined:=public.core_dispatch('join',jsonb_build_object('post_id',p->>'id','expected_version',1));
  result:=public.core_order_activity();
  assert jsonb_array_length(result->'items')=1 and result->'items'->0->>'kind'='my_request','requester sees own state';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  result:=public.core_order_activity();
  assert result->'items'->0->>'kind'='hosted_request' and result->'items'->0->>'status'='pending','host sees pending request';
  perform public.core_dispatch('decide',jsonb_build_object('join_id',joined->>'id','status','accepted'));
  assert public.core_order_activity()->'items'->0->>'status'='accepted','decision updates latest state';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',s.user_id,'session_id',s.session_id,'role','authenticated')::text,true);
  assert jsonb_array_length(public.core_order_activity()->'items')=0,'other user cannot read private request activity';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',m.user_id,'session_id',m.session_id,'role','authenticated')::text,true);
  perform public.core_dispatch('withdraw',jsonb_build_object('post_id',p->>'id'));
  assert public.core_order_activity()->'items'->0->>'status'='cancelled','withdrawal shown';
  begin
    perform public.core_order_activity(jsonb_build_object('actor',h.user_id));
    raise exception 'activity actor injection incorrectly allowed';
  exception when sqlstate 'PT422' then null; end;
  begin
    perform public.core_order_activity('{"cursor":"%%%"}');
    raise exception 'activity malformed cursor incorrectly allowed';
  exception when sqlstate 'PT422' then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  perform public.core_dispatch('cancel',jsonb_build_object('post_id',p->>'id'));
  assert (public.core_order_activity()->'items'->0->>'post_cancelled')::boolean,'cancelled post remains explicit';
end;
$test$;
rollback;
