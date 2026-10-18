-- Entire fixture and function preview must run inside a transaction and be rolled back.
begin;
create temporary table rec_v2_fixture(n integer, uid uuid, sid uuid);
insert into rec_v2_fixture select i,gen_random_uuid(),gen_random_uuid() from generate_series(1,4) i;
insert into auth.users(id,email,email_confirmed_at)
 select uid,uid::text||'@example.test',now() from rec_v2_fixture;
insert into auth.sessions(id,user_id) select sid,uid from rec_v2_fixture;
insert into public.core_profiles(id,display_name) select uid,'隔離推薦驗證'||n from rec_v2_fixture;
grant select on rec_v2_fixture to authenticated;
set local role authenticated;
do $test$
declare a record; pref jsonb; v jsonb; ids uuid[]:='{}'; p jsonb; page jsonb; second jsonb;
 third jsonb; i integer; first_cursor text; other record; msg text;
begin
 select * into a from rec_v2_fixture where n=1;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a.uid,'session_id',a.sid,'role','authenticated')::text,true);
 pref:=public.core_engagement('preferences_get')-'version';
 pref:=jsonb_set(pref,'{preferred_tags}','["隔離推薦v2"]');
 perform public.core_engagement('preferences_put',jsonb_build_object('expected_version',0,'value',pref));
 -- Build six fixtures as a different synthetic author, through the real dispatcher.
 select * into other from rec_v2_fixture where n=2;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',other.uid,'session_id',other.sid,'role','authenticated')::text,true);
 for i in 1..6 loop
  v:=jsonb_build_object('content','隔離推薦v2測試貼文，這不是真實訂單。'||i,'tags','["隔離推薦v2"]'::jsonb,
   'capacity',3,'deadline_at',clock_timestamp()+case when i=1 then interval '15 minutes' else interval '1 day' end,
   'price_per_person',null,'location',jsonb_build_object('name','虛構推薦集合點','latitude',24.987,'longitude',121.576));
  p:=public.core_dispatch('create',jsonb_build_object('value',v,'request_key','isolated-recommend-v2-'||i));
  ids:=array_append(ids,(p->>'id')::uuid);
 end loop;
 -- Privileged fixture setup is outside the public API, never affects existing rows.
 perform set_config('test.rec_ids',to_jsonb(ids)::text,true);
end $test$;
reset role;
do $setup$
declare ids uuid[]:=array(select jsonb_array_elements_text(current_setting('test.rec_ids')::jsonb)::uuid);
begin
 insert into public.core_join_requests(post_id,requester_id,status,decided_at)
 select ids[i],(select uid from rec_v2_fixture where n=3),'accepted',now() from unnest(array[1,2,4]) i;
 insert into public.core_join_requests(post_id,requester_id,status,decided_at)
 values(ids[4],(select uid from rec_v2_fixture where n=4),'accepted',now());
 update public.core_posts set cancelled=true where id=ids[5];
 update public.core_posts set deadline_at=now()-interval '1 hour' where id=ids[6];
end $setup$;
set local role authenticated;
do $verify$
declare a record; other record; ids uuid[]:=array(select jsonb_array_elements_text(current_setting('test.rec_ids')::jsonb)::uuid);
 page jsonb; second jsonb; third jsonb; cursor_value text;
begin
 select * into a from rec_v2_fixture where n=1;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a.uid,'session_id',a.sid,'role','authenticated')::text,true);
 page:=public.core_recommendation_feed('{"limit":1}');
 assert page->'items'->0->>'id'=ids[1]::text,'near deadline wins with same preference and seats';
 assert page->'items'->0->'recommendation'->>'score'='40';
 assert page->'items'->0->'recommendation'->>'remaining_seats'='1';
 assert jsonb_array_length(page->'items'->0->'recommendation'->'reasons')=3;
 cursor_value:=page->>'next_cursor';
 second:=public.core_recommendation_feed(jsonb_build_object('limit',1,'cursor',cursor_value));
 assert second->'items'->0->>'id'=ids[2]::text,'one remaining seat wins over two';
 third:=public.core_recommendation_feed(jsonb_build_object('limit',1,'cursor',second->>'next_cursor'));
 assert third->'items'->0->>'id'=ids[3]::text,'pagination retains third unique item';
 assert page->'items'->0->'recommendation'->>'as_of'=third->'items'->0->'recommendation'->>'as_of';
 page:=public.core_recommendation_feed('{"limit":50}');
 assert not exists(select 1 from jsonb_array_elements(page->'items') x where x->>'id'=any(array[ids[4],ids[5],ids[6]]::text[])),
  'full/cancelled/expired fixtures excluded';
 perform public.core_saved_posts('put',jsonb_build_object('post_id',ids[2],'hidden',true,'reminder',false));
 page:=public.core_recommendation_feed('{"limit":50}');
 assert not exists(select 1 from jsonb_array_elements(page->'items') x where x->>'id'=ids[2]::text),'hidden removed before pagination';
 begin
  perform public.core_recommendation_feed(jsonb_build_object('limit',1,'cursor',cursor_value));
  raise exception 'Changed inventory accepted stale cursor';
 exception when sqlstate 'PT409' then null; end;
 select * into other from rec_v2_fixture where n=3;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',other.uid,'session_id',other.sid,'role','authenticated')::text,true);
 begin
  perform public.core_recommendation_feed(jsonb_build_object('limit',1,'cursor',cursor_value));
  raise exception 'Other account accepted cursor';
 exception when sqlstate 'PT409' then null; end;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a.uid,'session_id',a.sid,'role','authenticated')::text,true);
 perform public.core_user_blocks('put',jsonb_build_object('user_id',(select uid from rec_v2_fixture where n=2)));
 page:=public.core_recommendation_feed('{"limit":50}');
 assert not exists(select 1 from jsonb_array_elements(page->'items') x
  where x->>'id'=any(ids::text[])),'blocked author excluded before pagination';
 assert not has_function_privilege('anon','public.core_recommendation_feed(jsonb)','EXECUTE');
end $verify$;
reset role;
rollback;
