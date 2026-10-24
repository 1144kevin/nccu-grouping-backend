-- Transaction-only fixtures; no real Storage bytes, messages, or persistent accounts.
begin;
create temporary table gallery_fixture(owner_id uuid,session_id uuid);
insert into gallery_fixture values(gen_random_uuid(),gen_random_uuid());
insert into auth.users(id,email,email_confirmed_at) select owner_id,owner_id::text||'@example.test',now() from gallery_fixture;
insert into auth.sessions(id,user_id) select session_id,owner_id from gallery_fixture;
insert into public.core_profiles(id,display_name) select owner_id,'相簿隔離測試' from gallery_fixture;
create temporary table gallery_assets(id uuid,object_id uuid,ordinal integer);
insert into gallery_assets select gen_random_uuid(),gen_random_uuid(),n from generate_series(1,6) n;
insert into public.core_media_assets(id,owner_id,request_key,sha256,width,height,state,verified_object_id)
 select a.id,f.owner_id,'gallery-smoke-upload-'||a.ordinal,repeat('a',64),
 case when a.ordinal%2=0 then 1200 else 800 end,case when a.ordinal%2=0 then 800 else 1200 end,'ready',a.object_id
 from gallery_assets a cross join gallery_fixture f;
insert into storage.objects(id,bucket_id,name,owner_id,metadata)
 select a.object_id,'core-post-images',f.owner_id::text||'/'||a.id::text||'.jpg',f.owner_id::text,
 '{"size":100,"mimetype":"image/jpeg"}'::jsonb from gallery_assets a cross join gallery_fixture f;
grant select on gallery_fixture,gallery_assets to authenticated;
set local role authenticated;
do $$ declare f record; ids jsonb; value jsonb; request jsonb; result jsonb; repeated jsonb; before_count integer; begin
 select * into f from gallery_fixture;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',f.owner_id,'session_id',f.session_id,'role','authenticated')::text,true);
 select jsonb_agg(id order by ordinal) into ids from gallery_assets where ordinal<=5;
 value:=jsonb_build_object('content','隔離測試：一起分享餐點與討論取餐，沒有真實訂購。',
 'tags',jsonb_build_array('拼單'),'deadline_at',clock_timestamp()+interval '2 hours','capacity',2,
 'price_per_person',null,'location',jsonb_build_object('name','政大正門','latitude',24.987,'longitude',121.576));
 request:=jsonb_build_object('image_ids',ids,'value',value,'request_key','gallery-smoke-publish-1');
 result:=public.core_media('publish_many',request);
 assert jsonb_array_length(result->'images')=5,'five images missing';
 assert result->>'image_id'=ids->>0,'legacy cover changed';
 assert result->'images'->0->>'id'=ids->>0 and result->'images'->4->>'id'=ids->>4,'selection order lost';
 assert (result->'images'->0->>'height')::integer=1200,'portrait dimensions lost';
 assert (result->'images'->1->>'width')::integer=1200,'landscape dimensions lost';
 repeated:=public.core_media('publish_many',request);
 assert repeated->>'id'=result->>'id','retry created duplicate';
 begin
  perform public.core_media('publish_many',request||jsonb_build_object('image_ids',jsonb_build_array(ids->0,ids->0)));
  raise exception 'duplicate images accepted';
 exception when sqlstate 'PT422' then null; end;
 begin
  perform public.core_media('publish_many',request||jsonb_build_object('image_ids',ids||jsonb_build_array((select id from gallery_assets where ordinal=6))));
  raise exception 'six images accepted';
 exception when sqlstate 'PT422' then null; end;
 begin
  perform public.core_media('publish_many',request||jsonb_build_object('image_ids',jsonb_build_array(ids->1,ids->0)));
  raise exception 'changed gallery reused request';
 exception when sqlstate 'PT409' then null; end;
 begin
  perform public.core_media('delete_prepare',jsonb_build_object('image_id',ids->>4));
  raise exception 'published image deletable';
 exception when sqlstate 'PT409' then null; end;
 begin
  perform public.core_media('publish_many',request||jsonb_build_object('request_key','gallery-smoke-publish-other'));
  raise exception 'asset reused across posts';
 exception when sqlstate 'PT409' then null; end;
 result:=public.core_media('publish',jsonb_build_object('image_id',(select id from gallery_assets where ordinal=6),
  'value',value,'request_key','gallery-smoke-single-legacy'));
 assert jsonb_array_length(result->'images')=1,'legacy publisher broken';
 assert not has_function_privilege('authenticated','core_private.media_before_gallery(text,jsonb)','EXECUTE'),'old publish bypass exposed';
end $$;
reset role;
rollback;
