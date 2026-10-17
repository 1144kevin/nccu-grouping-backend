-- Verify authorization and selective cleanup; all fixtures roll back.
begin;
create temporary table demo_fixture(name text,user_id uuid,session_id uuid);
insert into demo_fixture values('owner',gen_random_uuid(),gen_random_uuid()),
  ('person',gen_random_uuid(),gen_random_uuid()),('real_member',gen_random_uuid(),gen_random_uuid());
insert into auth.users(id,email,email_confirmed_at)
  select user_id,user_id::text||'@example.test',now() from demo_fixture;
insert into auth.sessions(id,user_id) select session_id,user_id from demo_fixture;
insert into public.core_profiles(id,display_name) select user_id,name from demo_fixture;
insert into core_private.demo_batches(batch,owner_id,people)
  select 'rollback_demo_test',o.user_id,array[p.user_id]
  from demo_fixture o cross join demo_fixture p where o.name='owner' and p.name='person';
insert into public.core_posts(author_id,payload,deadline_at,capacity,request_key,original_payload,demo_batch,demo_owner_id)
  select o.user_id,jsonb_build_object('content','清理權限測試，不保留資料'),now()+interval '1 day',4,
    'rollback_demo_'||n,'{}','rollback_demo_test',o.user_id
  from demo_fixture o cross join generate_series(1,2) n where o.name='owner';
insert into public.core_join_requests(post_id,requester_id,status)
  select p.id,f.user_id,'pending' from public.core_posts p cross join demo_fixture f
  where p.demo_batch='rollback_demo_test' and (
    (p.request_key='rollback_demo_1' and f.name='person') or
    (p.request_key='rollback_demo_2' and f.name='real_member'));
grant select on demo_fixture to authenticated;
set local role authenticated;
do $test$
declare u record; result jsonb;
begin
  assert not has_function_privilege('anon','public.core_clear_demo()','EXECUTE'),'anonymous cleanup denied';
  assert not has_table_privilege('authenticated','core_private.demo_batches','SELECT'),'manifest stays private';
  select * into u from demo_fixture where name='real_member';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',u.user_id,'session_id',u.session_id,'role','authenticated')::text,true);
  result:=public.core_clear_demo();
  assert (result->>'deleted_posts')::integer=0,'other user cannot clear owner demos';
  select * into u from demo_fixture where name='owner';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',u.user_id,'session_id',u.session_id,'role','authenticated')::text,true);
  result:=public.core_clear_demo();
  assert (result->>'deleted_posts')::integer=1,'only demo-only post deleted';
  assert (result->>'kept_posts')::integer=1,'real member post retained';
  assert (select count(id) from public.core_posts where author_id=u.user_id)=1,'real member data preserved';
  result:=public.core_clear_demo();
  assert (result->>'deleted_posts')::integer=0 and (result->>'kept_posts')::integer=1,'cleanup is repeatable';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',u.user_id,'session_id',gen_random_uuid(),'role','authenticated')::text,true);
  begin
    perform public.core_clear_demo();
    raise exception 'revoked session incorrectly accepted';
  exception when sqlstate 'PT401' then null; end;
end;
$test$;
rollback;
