-- All identities and messages roll back. Does not send mail or touch existing users.
begin;
create temporary table quick_fixture(n int,uid uuid,sid uuid,signal_id uuid,proposal_id uuid);
insert into quick_fixture(n,uid,sid) select n,gen_random_uuid(),gen_random_uuid() from generate_series(1,4) n;
insert into auth.users(id,email,email_confirmed_at,raw_app_meta_data)
 select uid,uid::text||'@example.test',now(),jsonb_build_object('stealth_test_scope','quick-smoke-'||(select uid::text from quick_fixture where n=4)) from quick_fixture;
insert into auth.sessions(id,user_id) select sid,uid from quick_fixture;
grant select,update on quick_fixture to authenticated;
set local role authenticated;
do $test$
declare f record; a record; b record; outsider record; v jsonb; p text; m jsonb; x jsonb; msg jsonb; k uuid:=gen_random_uuid();
begin
 select * into a from quick_fixture where n=1; select * into b from quick_fixture where n=2; select * into outsider from quick_fixture where n=4;
 for f in select * from quick_fixture loop
  perform set_config('request.jwt.claims',jsonb_build_object('sub',f.uid,'session_id',f.sid,'role','authenticated')::text,true);
  perform public.core_dispatch('me');
  perform public.core_stealth('agent_put',jsonb_build_object('expected_version',0,'value',jsonb_build_object('name','隔離快速分身','enabled',true,'include_demo',false,'daily_invite_limit',10,'timezone','Asia/Taipei','quiet_start_hour',null,'quiet_end_hour',null,'exploration','balanced','tone','warm','avatar_key','cat')));
  if f.n=4 then continue; end if;
  v:=jsonb_build_object('kind','need','category','drink','acceptable_categories','[]'::jsonb,'title','我今天想喝50嵐','location_id','main_gate','starts_at',now(),'ends_at',now()+interval '2 hours','expires_at',now()+interval '2 hours','budget_max_twd',null,'known_cost_twd',null,'tags',jsonb_build_array(case when f.n=3 then '品牌:清心' else '品牌:50嵐' end),'roles','[]'::jsonb,'dietary_requirements','[]'::jsonb,'dietary_verified','[]'::jsonb);
  x:=public.core_stealth('signal_create',jsonb_build_object('value',v,'request_key',f.sid));
  update quick_fixture set signal_id=(x->>'id')::uuid where n=f.n;
  assert public.core_stealth_quick('lookup',jsonb_build_object('request_key',f.sid))->>'id'=x->>'id','own retry lookup';
 end loop;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a.uid,'session_id',a.sid,'role','authenticated')::text,true);
 assert public.core_stealth_quick('lookup',jsonb_build_object('request_key',b.sid))='null'::jsonb,'no foreign lookup';
 m:=public.core_stealth('run',jsonb_build_object('max_results',5));
 assert (m->>'created')::int=1,'different explicit brands cannot match, even under balanced exploration';
 select value->>'id' into p from jsonb_array_elements(public.core_stealth('matches')->'items') limit 1;
 update quick_fixture set proposal_id=p::uuid;
 assert public.core_stealth_quick('state',jsonb_build_object('proposal_id',p))->>'opened'='false','no room before consent';
 perform public.core_stealth('decision',jsonb_build_object('proposal_id',p,'decision','accepted'));
 assert public.core_stealth_quick('state',jsonb_build_object('proposal_id',p))->>'opened'='false','one consent insufficient';
 perform set_config('request.jwt.claims',jsonb_build_object('sub',outsider.uid,'session_id',outsider.sid,'role','authenticated')::text,true);
 begin perform public.core_stealth_quick('state',jsonb_build_object('proposal_id',p)); raise exception 'outsider read allowed'; exception when sqlstate 'PT404' then null; end;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',b.uid,'session_id',b.sid,'role','authenticated')::text,true);
 perform public.core_stealth('decision',jsonb_build_object('proposal_id',p,'decision','accepted'));
 x:=public.core_stealth_quick('state',jsonb_build_object('proposal_id',p));
 assert x->>'opened'='true' and x->>'can_send'='true','unknown price and roles do not block preliminary chat';
 msg:=public.core_stealth_quick('send',jsonb_build_object('proposal_id',p,'content','我想喝無糖，可以討論誰去拿嗎？','request_key',k));
 assert public.core_stealth_quick('send',jsonb_build_object('proposal_id',p,'content','我想喝無糖，可以討論誰去拿嗎？','request_key',k))->>'id'=msg->>'id','same retry no duplicate';
 begin perform public.core_stealth_quick('send',jsonb_build_object('proposal_id',p,'content','changed','request_key',k)); raise exception 'idempotency mismatch allowed'; exception when sqlstate 'PT409' then null; end;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a.uid,'session_id',a.sid,'role','authenticated')::text,true);
 x:=public.core_stealth_quick('state',jsonb_build_object('proposal_id',p));
 assert jsonb_array_length(x->'items')=1 and x->'items'->0->>'is_me'='false','participant reads other member message';
 assert x::text not like '%'||b.uid::text||'%' and x::text not like '%example.test%','no email or account id disclosure';
 assert not exists(select 1 from jsonb_array_elements(public.core_stealth('matches')->'items') q(value) where q.value->>'status'='formed'),'chat does not create formal order';
end $test$;
reset role;

create temporary table photo_fixture(kind text,thread_id uuid,image_id uuid,object_id uuid,key uuid,proof text);
insert into public.core_posts(id,author_id,payload,original_payload,deadline_at,capacity,request_key)
select gen_random_uuid(),uid,'{"content":"照片測試拼單","tags":[],"location":{"name":"正門","latitude":25,"longitude":121},"deadline_at":"2099-01-01T00:00:00Z","capacity":3}', '{}','2099-01-01',3,'chat-photo-fixture' from quick_fixture where n=1;
insert into public.core_join_requests(post_id,requester_id,status) select p.id,b.uid,'accepted' from public.core_posts p,quick_fixture b where p.request_key='chat-photo-fixture' and b.n=2;
insert into public.core_direct_threads(post_id,host_id,guest_id) select p.id,p.author_id,b.uid from public.core_posts p,quick_fixture b where p.request_key='chat-photo-fixture' and b.n=2;
insert into photo_fixture(kind,thread_id,image_id,object_id,key)
select 'group',id,gen_random_uuid(),gen_random_uuid(),gen_random_uuid() from public.core_posts where request_key='chat-photo-fixture'
union all select 'direct',t.id,gen_random_uuid(),gen_random_uuid(),gen_random_uuid() from public.core_direct_threads t join public.core_posts p on p.id=t.post_id where p.request_key='chat-photo-fixture'
union all select 'stealth',proposal_id,gen_random_uuid(),gen_random_uuid(),gen_random_uuid() from quick_fixture where n=1;
insert into public.core_chat_images(id,owner_id,request_key,sha256,width,height)
select image_id,a.uid,'chat-photo-test-'||kind,repeat('a',64),20,20 from photo_fixture,quick_fixture a where a.n=1;
insert into storage.objects(id,bucket_id,name,owner_id,metadata)
select object_id,'core-chat-images',image_id::text||'.jpg',a.uid::text,'{"mimetype":"image/jpeg","size":128}' from photo_fixture,quick_fixture a where a.n=1;
update photo_fixture f set proof=encode(extensions.hmac(convert_to(f.image_id::text||':'||a.uid::text||':'||repeat('a',64)||':'||f.object_id::text,'UTF8'),k.secret,'sha256'),'hex')
from quick_fixture a,core_private.media_signing_keys k where a.n=1 and k.version=1;
grant select on photo_fixture to authenticated;
set local role authenticated;
do $$ declare a record; b record; outsider record; f record; v jsonb; again jsonb; begin
 select * into a from quick_fixture where n=1; select * into b from quick_fixture where n=2; select * into outsider from quick_fixture where n=4;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a.uid,'session_id',a.sid,'role','authenticated')::text,true);
 for f in select * from photo_fixture loop
  begin perform public.core_chat_media('ready',jsonb_build_object('image_id',f.image_id,'storage_id',f.object_id,'proof','wrong')); raise exception 'unattested photo accepted'; exception when sqlstate 'PT403' then null; end;
  perform public.core_chat_media('ready',jsonb_build_object('image_id',f.image_id,'storage_id',f.object_id,'proof',f.proof));
  v:=public.core_chat_photo('send',jsonb_build_object('kind',f.kind,'thread_id',f.thread_id,'image_id',f.image_id,'request_key',f.key));
  again:=public.core_chat_photo('send',jsonb_build_object('kind',f.kind,'thread_id',f.thread_id,'image_id',f.image_id,'request_key',f.key));
  assert v->>'id'=again->>'id','retry duplicates message';
  assert public.core_chat_photo('read',jsonb_build_object('image_id',f.image_id))->>'object_path'=f.image_id::text||'.jpg','opaque path';
  begin perform public.core_chat_media('delete_prepare',jsonb_build_object('image_id',f.image_id)); raise exception 'sent image deleted'; exception when sqlstate 'PT409' then null; end;
 end loop;
 assert jsonb_array_length(public.core_chat_media('list')->'items')=0,'sent photos listed as drafts';
 for f in select * from photo_fixture loop
  perform set_config('request.jwt.claims',jsonb_build_object('sub',b.uid,'session_id',b.sid,'role','authenticated')::text,true);
  v:=public.core_chat_photo('read',jsonb_build_object('image_id',f.image_id));
  assert v::text not like '%'||a.uid::text||'%' and v->>'id'=f.image_id::text,'member read or anonymity';
  if f.kind='group' then v:=public.core_chat('messages',jsonb_build_object('post_id',f.thread_id));
   assert exists(select 1 from jsonb_array_elements(v->'messages') m where m->>'image_id'=f.image_id::text),'group picture metadata';
  elsif f.kind='direct' then v:=public.core_social('messages',jsonb_build_object('thread_id',f.thread_id));
   assert exists(select 1 from jsonb_array_elements(v->'messages') m where m->>'image_id'=f.image_id::text),'direct picture metadata';
  else v:=public.core_stealth_quick('state',jsonb_build_object('proposal_id',f.thread_id));
   assert exists(select 1 from jsonb_array_elements(v->'items') m where m->>'image_id'=f.image_id::text),'stealth picture metadata';
  end if;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',outsider.uid,'session_id',outsider.sid,'role','authenticated')::text,true);
  begin perform public.core_chat_photo('read',jsonb_build_object('image_id',f.image_id)); raise exception 'outsider reads photo'; exception when sqlstate 'PT404' then null; end;
  assert not core_private.chat_media_access('select',f.image_id::text||'.jpg'),'storage bypass';
 end loop;
end $$;
reset role;
insert into public.core_user_blocks(owner_id,blocked_id) select b.uid,a.uid from quick_fixture a,quick_fixture b where a.n=1 and b.n=2;
set local role authenticated;
do $$ declare b record; f record; begin
 select * into b from quick_fixture where n=2;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',b.uid,'session_id',b.sid,'role','authenticated')::text,true);
 for f in select * from photo_fixture loop
  begin perform public.core_chat_photo('read',jsonb_build_object('image_id',f.image_id)); raise exception 'blocked photo readable'; exception when sqlstate 'PT404' then null; end;
 end loop;
end $$;
reset role;
rollback;
select 'chat photos: verified upload, three chat kinds, retries, member access, anonymous paths, blocks and draft protection passed';
