-- Synthetic fixtures, fully rolled back. No real accounts or Storage bytes persist.
begin;
create temporary table share_fixture(owner_id uuid,session_id uuid,post_id uuid,token text);
insert into share_fixture values(gen_random_uuid(),gen_random_uuid(),null,null);
insert into auth.users(id,email,email_confirmed_at)
 select owner_id,owner_id::text||'@example.test',now() from share_fixture;
insert into auth.sessions(id,user_id) select session_id,owner_id from share_fixture;
do $test$
declare f record; p jsonb; link jsonb; v jsonb; image uuid:=gen_random_uuid(); object_id uuid:=gen_random_uuid();
begin
 select * into f from share_fixture;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',f.owner_id,'session_id',f.session_id,'role','authenticated')::text,true);
 perform public.core_dispatch('me');
 p:=public.core_dispatch('create',jsonb_build_object('request_key','share-synthetic-post', 'value',jsonb_build_object(
  'content','一起訂晚餐，這是分享連結的隔離交易測試。','tags',jsonb_build_array('晚餐'),'capacity',3,
  'deadline_at',now()+interval '1 day','price_per_person','85','location',jsonb_build_object(
   'name','虛構測試點','latitude',24.987,'longitude',121.576,'address','private-address-not-in-share'))));
 link:=public.core_post_share('create',jsonb_build_object('post_id',p->>'id'));
 assert link->>'token' ~ '^[0-9a-f]{64}$';
 assert public.core_post_share('create',jsonb_build_object('post_id',p->>'id'))=link,'stable link on retry';
 update share_fixture set post_id=(p->>'id')::uuid,token=link->>'token';
 perform set_config('request.jwt.claims','{}',true);
 v:=public.core_post_share('read',link);
 assert v->>'content'=p->>'content';
 assert v->>'location_name'='虛構測試點';
 assert not(v ?| array['author_id','location','my_status','can_edit','version','email','members','requests','messages']);
 assert v::text not like '%private-address-not-in-share%';
 assert v->'images'='[]'::jsonb;
 begin perform public.core_post_share('create',jsonb_build_object('post_id',p->>'id'));
  raise exception 'anonymous share creation accepted'; exception when sqlstate 'PT401' then null; end;
 begin perform public.core_post_share('read',jsonb_build_object('token',repeat('0',64)));
  raise exception 'unknown token accepted'; exception when sqlstate 'PT404' then null; end;
 begin perform public.core_post_share('image',link||jsonb_build_object('image_id',gen_random_uuid()));
  raise exception 'unbound image accepted'; exception when sqlstate 'PT404' then null; end;
 perform set_config('request.headers',jsonb_build_object('x-post-share',link->>'token')::text,true);
 assert not public.core_shared_media_access('draft-or-chat.jpg');
 -- Only metadata is inserted, inside this rollback transaction; no cloud bytes are uploaded.
 insert into storage.objects(id,bucket_id,name,owner_id) values(object_id,'core-post-images',f.owner_id::text||'/'||image::text||'.jpg',f.owner_id::text);
 insert into public.core_media_assets(id,owner_id,request_key,sha256,width,height,state,verified_object_id)
  values(image,f.owner_id,'share-image-fixture',repeat('0',64),800,600,'ready',object_id);
 insert into public.core_post_images(post_id,asset_id,position) values((p->>'id')::uuid,image,1);
 assert public.core_shared_media_access(f.owner_id::text||'/'||image::text||'.jpg');
 perform set_config('request.headers','{}',true);
 assert not public.core_shared_media_access(f.owner_id::text||'/'||image::text||'.jpg');
 assert not has_table_privilege('anon','core_private.post_shares','SELECT');
 assert not has_function_privilege('anon','public.core_media(text,jsonb)','EXECUTE');
end $test$;
grant select on share_fixture to anon;
set local role anon;
do $test$
begin
 assert public.core_post_share('read',jsonb_build_object('token',(select token from share_fixture)))->>'location_name'='虛構測試點';
end $test$;
reset role;
update public.core_posts set cancelled=true where id=(select post_id from share_fixture);
set local role anon;
do $test$
begin
 begin perform public.core_post_share('read',jsonb_build_object('token',(select token from share_fixture)));
  raise exception 'cancelled share visible to anon'; exception when sqlstate 'PT404' then null; end;
end $test$;
rollback;
