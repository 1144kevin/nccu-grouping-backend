-- Synthetic fixtures, no changes persist. Run after the migration in isolation.
begin;
do $check$
declare a uuid:=gen_random_uuid(); b uuid:=gen_random_uuid(); moderator uuid:=gen_random_uuid();
 sa uuid:=gen_random_uuid(); sb uuid:=gen_random_uuid(); sm uuid:=gen_random_uuid();
 avatar uuid; evidence uuid; object_id uuid:=gen_random_uuid(); value jsonb; report jsonb; extra uuid;
begin
 insert into auth.users(id,email,email_confirmed_at) values(a,a||'@example.test',now()),(b,b||'@example.test',now()),(moderator,moderator||'@example.test',now());
 insert into auth.sessions(id,user_id) values(sa,a),(sb,b),(sm,moderator);
 insert into public.core_profiles(id,display_name) values(a,'主揪測試'),(b,'團員測試'),(moderator,'管理測試');
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a,'session_id',sa,'role','authenticated')::text,true);
 assert (public.core_profile_social('get',jsonb_build_object('user_id',b))->>'likes')::int=0;
 perform public.core_profile_social('like',jsonb_build_object('user_id',b,'liked',true));
 perform public.core_profile_social('like',jsonb_build_object('user_id',b,'liked',true));
 assert (public.core_profile_social('get',jsonb_build_object('user_id',b))->>'likes')::int=1,'retries do not add duplicate likes';
 assert core_private.reputation_score(b)=0,'fewer than ten likes do not add score';
 begin perform public.core_profile_social('like',jsonb_build_object('user_id',a,'liked',true)); raise exception 'self-like accepted'; exception when sqlstate 'PT422' then null; end;
 for n in 1..9 loop
  extra:=gen_random_uuid(); insert into auth.users(id,email) values(extra,extra||'@example.test');
  insert into public.core_profiles(id,display_name) values(extra,'額外按讚者');
  insert into public.core_profile_likes(actor_id,target_id) values(extra,b);
 end loop;
 assert core_private.reputation_score(b)=1,'ten distinct valid likes add one';
 insert into public.core_user_blocks(owner_id,blocked_id) values(a,b);
 assert core_private.reputation_score(b)=0,'blocked like does not count';
 begin perform public.core_profile_social('get',jsonb_build_object('user_id',b)); raise exception 'blocked interaction allowed'; exception when sqlstate 'PT404' then null; end;
 delete from public.core_user_blocks where owner_id=a and blocked_id=b;
 value:=public.core_personal_media('reserve',jsonb_build_object('request_key','avatar-synthetic-key','sha256',repeat('a',64),'width',100,'height',100,'purpose','avatar'));
 avatar:=(value->>'id')::uuid;
 assert core_private.personal_image_access('insert',avatar||'.jpg'),'owner can upload';
 begin perform public.core_personal_media('reserve',jsonb_build_object('request_key','avatar-synthetic-key','sha256',repeat('b',64),'width',100,'height',100,'purpose','avatar')); raise exception 'changed image replay accepted'; exception when sqlstate 'PT409' then null; end;
 -- Attestation is separately covered by the MediaService tests; simulate its ready output here.
 insert into storage.objects(id,bucket_id,name,owner_id,metadata) values(object_id,'core-personal-images',avatar||'.jpg',a::text,'{"mimetype":"image/jpeg","size":100}');
 update public.core_personal_images set state='ready',verified_object_id=object_id where id=avatar;
 perform core_private.dispatch('profile',jsonb_build_object('display_name','新名稱','bio','','badge','🍱','expected_version',1,'avatar_id',avatar));
 assert core_private.profile_view(a)->>'avatar_id'=avatar::text;
 assert not core_private.personal_image_access('delete',avatar||'.jpg'),'attached avatar cannot be deleted';
 value:=public.core_personal_media('reserve',jsonb_build_object('request_key','evidence-synthetic-key','sha256',repeat('a',64),'width',100,'height',100,'purpose','evidence'));
 evidence:=(value->>'id')::uuid;
 insert into storage.objects(id,bucket_id,name,owner_id,metadata) values(gen_random_uuid(),'core-personal-images',evidence||'.jpg',a::text,'{"mimetype":"image/jpeg","size":100}');
 update public.core_personal_images set state='ready',verified_object_id=(select id from storage.objects where name=evidence||'.jpg') where id=evidence;
 report:=jsonb_build_object('kind','user','target_id',b,'reason','harassment','detail','這是供隔離測試使用的具體回報說明。','platform','web','app_version','1.0.0');
 begin perform core_private.engagement('report',jsonb_build_object('request_key','report-synthetic-key','value',report)); raise exception 'report without picture accepted'; exception when sqlstate 'PT422' then null; end;
 report:=report||jsonb_build_object('evidence_image_id',avatar);
 begin perform core_private.engagement('report',jsonb_build_object('request_key','report-synthetic-key','value',report)); raise exception 'avatar used as evidence'; exception when sqlstate 'PT422' then null; end;
 report:=report||jsonb_build_object('evidence_image_id',evidence);
 value:=core_private.engagement('report',jsonb_build_object('request_key','report-synthetic-key','value',report));
 assert value=core_private.engagement('report',jsonb_build_object('request_key','report-synthetic-key','value',report)),'report retry is idempotent';
 assert not core_private.personal_image_access('delete',evidence||'.jpg'),'attached evidence cannot be removed';
 perform set_config('request.jwt.claims',jsonb_build_object('sub',b,'session_id',sb,'role','authenticated')::text,true);
 assert core_private.personal_image_access('select',avatar||'.jpg'),'profile avatar can be seen';
 assert not core_private.personal_image_access('select',evidence||'.jpg'),'reported person cannot see evidence';
 begin perform public.core_personal_media('read',jsonb_build_object('image_id',evidence)); raise exception 'evidence privacy bypass'; exception when sqlstate 'PT404' then null; end;
 begin perform core_private.dispatch('profile',jsonb_build_object('display_name','團員','bio','','badge','🍱','expected_version',1,'avatar_id',avatar)); raise exception 'foreign avatar attach'; exception when sqlstate 'PT422' then null; end;
 insert into public.core_moderators(user_id,enabled,expires_at) values(moderator,true,now()+interval '1 hour');
 perform set_config('request.jwt.claims',jsonb_build_object('sub',moderator,'session_id',sm,'role','authenticated')::text,true);
 assert core_private.personal_image_access('select',evidence||'.jpg'),'active moderator can inspect attached evidence';
 -- Even a previously attested asset cannot be attached after its object disappears.
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a,'session_id',sa,'role','authenticated')::text,true);
 delete from storage.objects where name=evidence||'.jpg';
 begin perform core_private.engagement('report',jsonb_build_object('request_key','report-deleted-image','value',report)); raise exception 'missing evidence accepted'; exception when sqlstate 'PT409' then null; end;
 assert not has_function_privilege('anon','public.core_personal_media(text,jsonb)','EXECUTE');
 assert not has_function_privilege('anon','public.core_profile_social(text,jsonb)','EXECUTE');
end $check$;
rollback;
