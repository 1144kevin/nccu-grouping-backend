begin;
create temporary table saved_fixture(name text,user_id uuid,session_id uuid);
insert into saved_fixture values('a',gen_random_uuid(),gen_random_uuid()),('b',gen_random_uuid(),gen_random_uuid());
insert into auth.users(id,email,email_confirmed_at) select user_id,user_id::text||'@example.test',now() from saved_fixture;
insert into auth.sessions(id,user_id) select session_id,user_id from saved_fixture;
grant select on saved_fixture to authenticated;
set local role authenticated;
do $test$
declare a record;b record;p jsonb;v jsonb;
begin
 select * into a from saved_fixture where name='a';select * into b from saved_fixture where name='b';
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a.user_id,'session_id',a.session_id,'role','authenticated')::text,true);
 perform public.core_dispatch('me');
 p:=public.core_dispatch('create',jsonb_build_object('request_key','saved-smoke-post','value',jsonb_build_object(
  'content','僅供隔離交易測試的前景截止提醒。','tags',jsonb_build_array('驗證'),'capacity',2,
  'deadline_at',clock_timestamp()+interval '20 minutes','price_per_person',null,
  'location',jsonb_build_object('name','虛構測試點','latitude',24.987,'longitude',121.576))));
 v:=public.core_saved_posts('put',jsonb_build_object('post_id',p->>'id','hidden',true,'reminder',true));
 assert v->>'status'='due';assert (v->>'hidden')::boolean;
 assert jsonb_array_length(public.core_saved_posts('list')->'items')=1;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',b.user_id,'session_id',b.session_id,'role','authenticated')::text,true);
 perform public.core_dispatch('me');
 assert jsonb_array_length(public.core_saved_posts('list')->'items')=0;
 begin
  perform public.core_saved_posts('put',jsonb_build_object('post_id',p->>'id','hidden','true','reminder',false));raise exception 'string boolean accepted';
 exception when sqlstate 'PT422' then null;end;
 begin
  perform public.core_saved_posts('list',jsonb_build_object('owner_id',a.user_id));raise exception 'forged owner accepted';
 exception when sqlstate 'PT422' then null;end;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a.user_id,'session_id',a.session_id,'role','authenticated')::text,true);
 perform public.core_dispatch('cancel',jsonb_build_object('post_id',p->>'id'));
 v:=public.core_saved_posts('put',jsonb_build_object('post_id',p->>'id','hidden',false,'reminder',true));
 assert v->>'status'='cancelled';
 v:=public.core_saved_posts('put',jsonb_build_object('post_id',p->>'id','hidden',false,'reminder',false));
 assert not (v->>'reminder')::boolean;assert jsonb_array_length(public.core_saved_posts('list')->'items')=0;
 begin
  perform public.core_saved_posts('put',jsonb_build_object('post_id',p->>'id','hidden',false,'reminder',true));raise exception 'new reminder on cancelled post accepted';
 exception when sqlstate 'PT409' then null;end;
 assert not has_function_privilege('anon','public.core_saved_posts(text,jsonb)','EXECUTE');
 assert not has_table_privilege('authenticated','public.core_saved_post_preferences','SELECT');
end $test$;
reset role;
rollback;
