-- Five synthetic authenticated actors against the actual deployed functions.
-- All identities, posts, image metadata, shares and messages roll back together.
begin;
create temporary table five_users(n integer,uid uuid,sid uuid,pid uuid,dm uuid);
insert into five_users select n,gen_random_uuid(),gen_random_uuid(),null,null from generate_series(1,5) n;
insert into auth.users(id,email,email_confirmed_at,raw_user_meta_data)
 select uid,uid::text||'@five-user.invalid',now(),jsonb_build_object('display_name','隔離測試'||n) from five_users;
insert into auth.sessions(id,user_id) select sid,uid from five_users;
insert into public.core_profiles(id,display_name) select uid,'隔離測試'||n from five_users;
create temporary table five_assets(n integer,ordinal integer,id uuid,object_id uuid);
insert into five_assets select n,i,gen_random_uuid(),gen_random_uuid() from generate_series(1,5) n cross join generate_series(1,5) i;
insert into storage.objects(id,bucket_id,name,owner_id,metadata)
 select a.object_id,'core-post-images',f.uid::text||'/'||a.id::text||'.jpg',f.uid::text,'{"size":100,"mimetype":"image/jpeg"}'::jsonb
 from five_assets a join five_users f using(n);
insert into public.core_media_assets(id,owner_id,request_key,sha256,width,height,state,verified_object_id)
 select a.id,f.uid,'five-user-'||a.id,encode(sha256(a.id::text::bytea),'hex'),800,600,'ready',a.object_id
 from five_assets a join five_users f using(n);
grant select,update on five_users to authenticated;
grant select on five_assets to authenticated;
set local role authenticated;
do $test$
declare f record; host record; value jsonb; post jsonb; joined jsonb; msg jsonb; room jsonb; link jsonb; ids jsonb; main_post uuid; foreign_room uuid;
begin
 for f in select * from five_users order by n loop
  perform set_config('request.jwt.claims',jsonb_build_object('sub',f.uid,'session_id',f.sid,'role','authenticated')::text,true);
  assert public.core_dispatch('me')->>'display_name'='隔離測試'||f.n;
  select jsonb_agg(id order by ordinal) into ids from five_assets where n=f.n;
  value:=jsonb_build_object('content','五人隔離測試：一起協調餐點與取餐，沒有真實訂購。','tags',jsonb_build_array('隔離測試'),
   'capacity',5,'deadline_at',now()+interval '2 hours','price_per_person','50',
   'location',jsonb_build_object('name','政大正門','latitude',24.987,'longitude',121.576));
  post:=public.core_media('publish_many',jsonb_build_object('image_ids',ids,'value',value,'request_key','five-publish-'||f.uid));
  assert jsonb_array_length(post->'images')=5,'album count';
  assert post->>'my_status'='host','host management permission';
  update five_users set pid=(post->>'id')::uuid where n=f.n;
 end loop;
 select * into host from five_users where n=1;
 main_post:=host.pid;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',host.uid,'session_id',host.sid,'role','authenticated')::text,true);
 post:=public.core_dispatch('edit',jsonb_build_object('post_id',main_post,'value',value||jsonb_build_object('content','五人隔離測試：主揪在申請前調整拼單內容。'),'expected_version',1));
 assert post->>'version'='2','edit own post';
 for f in select * from five_users where n>1 order by n loop
  perform set_config('request.jwt.claims',jsonb_build_object('sub',f.uid,'session_id',f.sid,'role','authenticated')::text,true);
  assert jsonb_array_length(public.core_dispatch('post',jsonb_build_object('post_id',main_post))->'images')=5,'non-home detail album';
  joined:=public.core_dispatch('join',jsonb_build_object('post_id',main_post,'expected_version',2));
  assert joined->>'status'='pending';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',host.uid,'session_id',host.sid,'role','authenticated')::text,true);
  assert public.core_dispatch('decide',jsonb_build_object('join_id',joined->>'id','status','accepted'))->>'status'='accepted';
 end loop;
 assert public.core_dispatch('post',jsonb_build_object('post_id',main_post))->>'joined'='5','all five joined';
 for f in select * from five_users order by n loop
  perform set_config('request.jwt.claims',jsonb_build_object('sub',f.uid,'session_id',f.sid,'role','authenticated')::text,true);
  msg:=public.core_chat('send',jsonb_build_object('post_id',main_post,'content','隔離訊息'||f.n,'client_key','five-chat-'||f.uid));
  assert public.core_chat('send',jsonb_build_object('post_id',main_post,'content','隔離訊息'||f.n,'client_key','five-chat-'||f.uid))->>'id'=msg->>'id','no duplicate message';
  if f.n>1 then
   room:=public.core_social('start',jsonb_build_object('post_id',main_post));
   update five_users set dm=(room->>'id')::uuid where n=f.n;
   perform public.core_social('send',jsonb_build_object('thread_id',room->>'id','content','私訊隔離測試','client_key','five-dm-'||f.uid));
  end if;
 end loop;
 for f in select * from five_users order by n loop
  perform set_config('request.jwt.claims',jsonb_build_object('sub',f.uid,'session_id',f.sid,'role','authenticated')::text,true);
  assert jsonb_array_length(public.core_chat('messages',jsonb_build_object('post_id',main_post))->'messages')=5,'all five group messages visible';
 end loop;
 select * into f from five_users where n=2;
 select dm into foreign_room from five_users where n=3;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',f.uid,'session_id',f.sid,'role','authenticated')::text,true);
 begin
  perform public.core_social('messages',jsonb_build_object('thread_id',foreign_room));
  raise exception 'foreign private message was exposed';
 exception when sqlstate 'PT404' then null; end;
 post:=public.core_dispatch('withdraw',jsonb_build_object('post_id',main_post));
 assert post->>'joined'='4','withdraw releases seat';
 perform set_config('request.jwt.claims',jsonb_build_object('sub',host.uid,'session_id',host.sid,'role','authenticated')::text,true);
 link:=public.core_post_share('create',jsonb_build_object('post_id',main_post));
 perform set_config('request.jwt.claims','{}',true);
 assert jsonb_array_length(public.core_post_share('read',link)->'images')=5,'guest share album';
 assert not(public.core_post_share('read',link) ? 'author_id'),'guest privacy';
 perform set_config('request.jwt.claims',jsonb_build_object('sub',host.uid,'session_id',host.sid,'role','authenticated')::text,true);
 assert public.core_dispatch('cancel',jsonb_build_object('post_id',main_post))->>'status'='cancelled';
 assert not(public.core_chat('messages',jsonb_build_object('post_id',main_post))->>'can_send')::boolean,'cancelled room read only';
end $test$;
reset role;
rollback;
select jsonb_build_object('passed',true,'actors',5,'published_albums',5,'image_metadata',25,'group_messages',5,'private_threads',4,'persistent_test_data',0) as five_user_result;
