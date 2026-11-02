begin;
do $check$
declare actor uuid:=gen_random_uuid(); other uuid:=gen_random_uuid(); sa uuid:=gen_random_uuid(); sb uuid:=gen_random_uuid();
 target uuid:=gen_random_uuid(); foreign_post uuid:=gen_random_uuid(); a uuid:=gen_random_uuid(); b uuid:=gen_random_uuid(); c uuid:=gen_random_uuid();
 object_a uuid:=gen_random_uuid(); object_b uuid:=gen_random_uuid(); object_c uuid:=gen_random_uuid(); payload jsonb; changed jsonb; value jsonb;
begin
 insert into auth.users(id,email,email_confirmed_at) values(actor,actor||'@example.test',now()),(other,other||'@example.test',now());
 insert into auth.sessions(id,user_id) values(sa,actor),(sb,other);
 insert into public.core_profiles(id,display_name) values(actor,'測試主揪'),(other,'其他人');
 payload:=jsonb_build_object('content','這是一筆可編輯的測試拼單','tags',jsonb_build_array('午餐'),'location',jsonb_build_object('name','測試地點','latitude',24.98,'longitude',121.57),'deadline_at',now()+interval '2 hours','capacity',3,'price_per_person','80');
 payload:=core_private.post_input(payload);
 insert into public.core_posts(id,author_id,payload,deadline_at,capacity,request_key,original_payload) values(target,actor,payload,now()+interval '2 hours',3,'fixture-edit-target',payload),(foreign_post,actor,payload,now()+interval '2 hours',3,'fixture-edit-other',payload);
 insert into public.core_media_assets(id,owner_id,request_key,sha256,width,height,state,verified_object_id) values
  (a,actor,'synthetic-image-a',repeat('a',64),100,100,'ready',object_a),
  (b,actor,'synthetic-image-b',repeat('b',64),100,100,'ready',object_b),
  (c,other,'synthetic-image-c',repeat('c',64),100,100,'ready',object_c);
 insert into storage.objects(id,bucket_id,name,owner_id,metadata) values
  (object_a,'core-post-images',actor||'/'||a||'.jpg',actor::text,'{"mimetype":"image/jpeg","size":100}'),
  (object_b,'core-post-images',actor||'/'||b||'.jpg',actor::text,'{"mimetype":"image/jpeg","size":100}'),
  (object_c,'core-post-images',other||'/'||c||'.jpg',other::text,'{"mimetype":"image/jpeg","size":100}');
 insert into public.core_post_images(post_id,asset_id,position) values(target,a,1);
 perform set_config('request.jwt.claims',jsonb_build_object('sub',actor,'session_id',sa,'role','authenticated')::text,true);
 changed:=payload||jsonb_build_object('content','修改後的完整拼單內容，保留其他設定');
 value:=public.core_edit_post_media(jsonb_build_object('post_id',target,'expected_version',1,'value',changed,'image_ids',jsonb_build_array(b,a)));
 assert (value->>'version')::int=2 and value->>'content'=changed->>'content';
 assert (select array_agg(asset_id order by position) from public.core_post_images where post_id=target)=array[b,a],'ordered gallery saved';
 assert (public.core_edit_post_media(jsonb_build_object('post_id',target,'expected_version',1,'value',changed,'image_ids',jsonb_build_array(b,a)))->>'version')::int=2,'retry is idempotent';
 begin perform public.core_edit_post_media(jsonb_build_object('post_id',target,'expected_version',2,'value',payload,'image_ids',jsonb_build_array(c)));raise exception 'foreign image accepted';exception when sqlstate 'PT404' then null;end;
 assert (select version from public.core_posts where id=target)=2,'failed gallery change rolls back text';
 begin perform public.core_edit_post_media(jsonb_build_object('post_id',target,'expected_version',2,'value',payload,'image_ids',jsonb_build_array(a,a)));raise exception 'duplicate image accepted';exception when sqlstate 'PT422' then null;end;
 value:=public.core_edit_post_media(jsonb_build_object('post_id',target,'expected_version',2,'value',changed,'image_ids','[]'::jsonb));
 assert not exists(select 1 from public.core_post_images where post_id=target),'can remove all photos';
 insert into public.core_post_images(post_id,asset_id,position) values(foreign_post,a,1);
 begin perform public.core_edit_post_media(jsonb_build_object('post_id',target,'expected_version',3,'value',payload,'image_ids',jsonb_build_array(a)));raise exception 'already published image accepted';exception when sqlstate 'PT409' then null;end;
 insert into public.core_join_requests(post_id,requester_id,status) values(target,other,'pending');
 begin perform public.core_edit_post_media(jsonb_build_object('post_id',target,'expected_version',3,'value',payload,'image_ids',jsonb_build_array(b)));raise exception 'active request edit accepted';exception when sqlstate 'PT409' then null;end;
 assert not exists(select 1 from public.core_post_images where post_id=target),'denied save does not attach photos';
 perform set_config('request.jwt.claims',jsonb_build_object('sub',other,'session_id',sb,'role','authenticated')::text,true);
 begin perform public.core_edit_post_media(jsonb_build_object('post_id',target,'expected_version',3,'value',payload,'image_ids','[]'::jsonb));raise exception 'non-host edit accepted';exception when sqlstate 'PT403' then null;end;
 assert not has_function_privilege('anon','public.core_edit_post_media(jsonb)','EXECUTE');
end $check$;
rollback;
