-- Transaction-only synthetic fixtures. Nothing in auth or application tables persists.
begin;
create temporary table core_fixture (name text, user_id uuid, session_id uuid, post_id uuid, request_id uuid);
insert into core_fixture(name,user_id,session_id) values
  ('host',gen_random_uuid(),gen_random_uuid()),('member',gen_random_uuid(),gen_random_uuid()),('other',gen_random_uuid(),gen_random_uuid());
insert into auth.users(id,email,raw_user_meta_data,email_confirmed_at)
  select user_id,user_id::text||'@example.test',jsonb_build_object('display_name',name),now() from core_fixture;
insert into auth.sessions(id,user_id) select session_id,user_id from core_fixture;
grant select,update on core_fixture to authenticated;
set local role authenticated;
do $test$
declare host record; member record; other_user record; value jsonb; p jsonb; joined jsonb; current_post uuid; request_id uuid;
begin
  select * into host from core_fixture where name='host';
  select * into member from core_fixture where name='member';
  select * into other_user from core_fixture where name='other';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',host.user_id,'session_id',host.session_id,'role','authenticated')::text,true);
  assert public.core_dispatch('me')->>'auth_mode'='supabase','cloud identity';
  assert not has_function_privilege('anon','public.core_dispatch(text,jsonb)','EXECUTE'),'anonymous RPC denied';
  assert not has_function_privilege('authenticated','core_private.profile_view(uuid)','EXECUTE'),'private email helper denied';
  begin
    perform public.core_dispatch('posts',jsonb_build_object('cursor','%%%bad-base64%%%'));
    raise exception 'malformed cursor incorrectly accepted';
  exception when sqlstate 'PT422' then null; end;
  value:=jsonb_build_object('content','雲端交易測試：兩人一起訂晚餐。','tags',jsonb_build_array('晚餐'),
    'capacity',2,'deadline_at',now()+interval '1 day','price_per_person','85.50',
    'location',jsonb_build_object('name','虛構測試點','latitude',24.987,'longitude',121.576));
  p:=public.core_dispatch('create',jsonb_build_object('value',value,'request_key','transaction-test-key'));
  current_post:=(p->>'id')::uuid;
  assert (p->>'joined')::integer=1 and (p->>'can_edit')::boolean,'host seat and editable';
  assert public.core_dispatch('create',jsonb_build_object('value',value,'request_key','transaction-test-key'))->>'id'=p->>'id','idempotent create';
  begin
    perform public.core_dispatch('create',jsonb_build_object('request_key','transaction-test-key'));
    raise exception 'missing payload bypassed replay validation';
  exception when sqlstate 'PT409' then null; end;
  begin
    perform public.core_dispatch('create',jsonb_build_object('value',value||jsonb_build_object('tags',true),'request_key','malformed-payload-key'));
    raise exception 'malformed tags incorrectly accepted';
  exception when sqlstate 'PT422' then null; end;
  value:=value||jsonb_build_object('content','雲端交易測試：修改內容後再申請加入。');
  p:=public.core_dispatch('edit',jsonb_build_object('post_id',current_post,'value',value,'expected_version',1));
  assert (p->>'version')::integer=2,'edit version';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',member.user_id,'session_id',member.session_id,'role','authenticated')::text,true);
  perform public.core_dispatch('me');
  begin
    perform public.core_dispatch('join',jsonb_build_object('post_id',current_post,'expected_version',1));
    raise exception 'stale join incorrectly allowed';
  exception when sqlstate 'PT409' then null; end;
  joined:=public.core_dispatch('join',jsonb_build_object('post_id',current_post,'expected_version',2));
  request_id:=(joined->>'id')::uuid;
  assert joined->>'status'='pending','pending application';
  assert (public.core_dispatch('post',jsonb_build_object('post_id',current_post))->>'joined')::integer=1,'pending consumes no seat';
  begin
    perform public.core_dispatch('requests',jsonb_build_object('post_id',current_post));
    raise exception 'nonhost read request names';
  exception when sqlstate 'PT403' then null; end;
  begin
    update public.core_posts set capacity=20 where id=current_post;
    raise exception 'direct write incorrectly allowed';
  exception when insufficient_privilege then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',host.user_id,'session_id',host.session_id,'role','authenticated')::text,true);
  begin
    perform public.core_dispatch('edit',jsonb_build_object('post_id',current_post,'value',value,'expected_version',2));
    raise exception 'editing pending conditions incorrectly allowed';
  exception when sqlstate 'PT409' then null; end;
  joined:=public.core_dispatch('decide',jsonb_build_object('join_id',request_id,'status','accepted'));
  assert joined->>'status'='accepted','host accepts';
  p:=public.core_dispatch('post',jsonb_build_object('post_id',current_post));
  assert (p->>'joined')::integer=2 and p->>'status'='full','capacity full';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',other_user.user_id,'session_id',other_user.session_id,'role','authenticated')::text,true);
  perform public.core_dispatch('me');
  begin
    perform public.core_dispatch('join',jsonb_build_object('post_id',current_post,'expected_version',2));
    raise exception 'full post incorrectly allowed join';
  exception when sqlstate 'PT409' then null; end;
  assert not exists(select 1 from public.core_join_requests where post_id=current_post),'RLS hides others applications';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',member.user_id,'session_id',member.session_id,'role','authenticated')::text,true);
  p:=public.core_dispatch('withdraw',jsonb_build_object('post_id',current_post));
  assert (p->>'joined')::integer=1 and p->>'my_status'='cancelled','withdraw releases seat';
  assert jsonb_array_length(public.core_dispatch('posts',jsonb_build_object('scope','participating'))->'items')=0,'membership list updates';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',host.user_id,'session_id',host.session_id,'role','authenticated')::text,true);
  p:=public.core_dispatch('profile',jsonb_build_object('display_name','更新測試主揪','bio','虛構介紹','badge','🍕','expected_version',1));
  assert (p->>'version')::integer=2 and p->>'badge'='🍕','profile saves';
  assert public.core_dispatch('post',jsonb_build_object('post_id',current_post))->>'author_name'='更新測試主揪','author name reflects profile';
  p:=public.core_dispatch('cancel',jsonb_build_object('post_id',current_post));
  assert p->>'status'='cancelled','cancel closes';
  assert not exists(select 1 from jsonb_array_elements(public.core_dispatch('posts',jsonb_build_object('active_only',true))->'items') item
    where item->>'id'=current_post::text),'active filter';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',host.user_id,'session_id',gen_random_uuid(),'role','authenticated')::text,true);
  begin
    perform public.core_dispatch('me');
    raise exception 'nonexistent session incorrectly accepted';
  exception when sqlstate 'PT401' then null; end;
  update core_fixture set post_id=current_post where name='host';
end;
$test$;
reset role;
select 'cloud transaction / RLS / approval / edit / withdrawal / profile: passed' as result;
rollback;
