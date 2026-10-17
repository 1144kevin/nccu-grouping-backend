begin;
create temporary table public_user_fixture(name text,user_id uuid,session_id uuid);
insert into public_user_fixture values('first',gen_random_uuid(),gen_random_uuid()),('second',gen_random_uuid(),gen_random_uuid());
insert into auth.users(id,email,email_confirmed_at,raw_user_meta_data)
  select user_id,user_id::text||'@example.test',now(),'{"display_name":"同名測試"}'::jsonb from public_user_fixture;
insert into auth.sessions(id,user_id) select session_id,user_id from public_user_fixture;
grant select on public_user_fixture to authenticated;
set local role authenticated;
do $test$
declare first_user record; second_user record; p jsonb; other_post jsonb; value jsonb; public_profile jsonb;
begin
  select * into first_user from public_user_fixture where name='first';
  select * into second_user from public_user_fixture where name='second';
  assert not has_function_privilege('anon','public.core_public_user(text,jsonb)','EXECUTE'),'anonymous profile RPC denied';
  value:=jsonb_build_object('content','公開個人資料測試：同名主揪也不能混淆拼單。','tags',jsonb_build_array('個人資料測試'),
    'capacity',3,'deadline_at',now()+interval '1 day','price_per_person','50.00',
    'location',jsonb_build_object('name','虛構測試點','latitude',24.987,'longitude',121.576));
  perform set_config('request.jwt.claims',jsonb_build_object('sub',first_user.user_id,'session_id',first_user.session_id,'role','authenticated')::text,true);
  p:=public.core_dispatch('create',jsonb_build_object('value',value,'request_key','public-first-post'));
  perform set_config('request.jwt.claims',jsonb_build_object('sub',second_user.user_id,'session_id',second_user.session_id,'role','authenticated')::text,true);
  other_post:=public.core_dispatch('create',jsonb_build_object('value',value,'request_key','public-second-post'));
  public_profile:=public.core_public_user('profile',jsonb_build_object('user_id',first_user.user_id));
  assert public_profile->>'display_name'='同名測試' and (public_profile->>'active_posts')::integer=1,'real public fields';
  assert public_profile-array['id','display_name','bio','badge','created_at','active_posts']='{}'::jsonb,'no private email or auth fields';
  value:=public.core_public_user('posts',jsonb_build_object('user_id',first_user.user_id));
  assert jsonb_array_length(value->'items')=1 and value->'items'->0->>'id'=p->>'id','same-name authors kept separate by UUID';
  assert value->'items'->0->>'my_status'='none','membership uses viewer identity';
  assert abs(extract(epoch from ((public.core_server_clock()->>'server_time')::timestamptz-clock_timestamp())))<2,'deadline clock follows database';
  begin
    perform public.core_public_user('profile',jsonb_build_object('user_id',gen_random_uuid()));
    raise exception 'unknown user incorrectly returned fabricated profile';
  exception when sqlstate 'PT404' then null; end;
  begin
    perform public.core_public_user('profile',jsonb_build_object('user_id',first_user.user_id,'actor',first_user.user_id));
    raise exception 'actor injection incorrectly allowed';
  exception when sqlstate 'PT422' then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',second_user.user_id,'session_id',gen_random_uuid(),'role','authenticated')::text,true);
  begin
    perform public.core_public_user('profile',jsonb_build_object('user_id',first_user.user_id));
    raise exception 'revoked session incorrectly read profile';
  exception when sqlstate 'PT401' then null; end;
end;
$test$;
rollback;
