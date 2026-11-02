-- Isolated Auth/profile/post fixtures. All mutations roll back.
begin;
do $check$
declare host uuid:=gen_random_uuid(); first_member uuid:=gen_random_uuid(); newcomer uuid:=gen_random_uuid();
 hs uuid:=gen_random_uuid(); fs uuid:=gen_random_uuid(); ns uuid:=gen_random_uuid();
 post uuid:=gen_random_uuid(); request_one uuid:=gen_random_uuid(); request_two uuid:=gen_random_uuid();
 old_message uuid:=gen_random_uuid(); fresh_message uuid:=gen_random_uuid(); photo uuid:=gen_random_uuid();
 device uuid:=gen_random_uuid(); proof text:=repeat('s',43); v jsonb; score_before integer;
begin
 insert into auth.users(id,email,email_confirmed_at) values(host,host||'@example.test',now()),(first_member,first_member||'@example.test',now()),(newcomer,newcomer||'@example.test',now());
 insert into auth.sessions(id,user_id) values(hs,host),(fs,first_member),(ns,newcomer);
 insert into public.core_profiles(id,display_name) values(host,'測試主揪'),(first_member,'測試離開者'),(newcomer,'測試新團員');
 insert into public.core_posts(id,author_id,payload,deadline_at,capacity,request_key,original_payload)
 values(post,host,jsonb_build_object('content','隔離測試的分享餐','tags',jsonb_build_array('拼單'),'location',jsonb_build_object('name','測試點','latitude',24.987,'longitude',121.576),'deadline_at',now()+interval '2 hours','capacity',2,'price_per_person','100'),now()+interval '2 hours',2,'test-'||post,'{}');
 insert into public.core_chat_messages(id,post_id,sender_id,content,client_key) values(old_message,post,host,'加入前的私密訊息','old-'||post);
 insert into public.core_join_requests(id,post_id,requester_id,status,decided_at) values(request_one,post,first_member,'accepted',clock_timestamp());
 update public.core_join_requests set status='cancelled' where id=request_one;
 insert into public.core_join_requests(id,post_id,requester_id,status,decided_at) values(request_two,post,newcomer,'accepted',clock_timestamp());
 perform set_config('request.jwt.claims',jsonb_build_object('sub',newcomer,'session_id',ns,'role','authenticated')::text,true);
 v:=public.core_chat('messages',jsonb_build_object('post_id',post));
 assert not exists(select 1 from jsonb_array_elements(v->'messages') m where m->>'id'=old_message::text),'newcomer cannot see prior messages';
 assert exists(select 1 from jsonb_array_elements(v->'messages') m where m->>'event_kind'='joined'),'join event is visible';
 assert core_private.visible_unread(newcomer,post)=0,'previous history is not unread';
 insert into public.core_chat_images(id,owner_id,request_key,sha256,width,height,state) values(photo,host,'old-photo-'||photo,repeat('a',64),32,32,'ready');
 insert into public.core_chat_photos(asset_id,owner_id,kind,thread_id,message_id,request_key) values(photo,host,'group',post,old_message,gen_random_uuid());
 assert not core_private.chat_photo_access((select p from public.core_chat_photos p where asset_id=photo),newcomer),'old image cannot be fetched directly';
 begin
  perform public.core_chat_activity('read',jsonb_build_object('post_id',post,'message_id',old_message));
  raise exception 'Hidden message read was accepted';
 exception when sqlstate 'PT404' then null; end;
 insert into public.core_chat_messages(id,post_id,sender_id,content,client_key) values(fresh_message,post,host,'加入後的新訊息','new-'||post);
 assert core_private.visible_unread(newcomer,post)=1,'new message is unread';
 v:=public.core_chat_activity('status'); assert (v->>'accepted_requests')::int=1,'accepted application has badge';
 perform public.core_notifications('read_orders',jsonb_build_object('post_id',post));
 assert (public.core_chat_activity('status')->>'accepted_requests')::int=0,'opening order clears acceptance badge';
 perform public.core_chat_activity('read',jsonb_build_object('post_id',post,'message_id',fresh_message));
 assert core_private.visible_unread(newcomer,post)=0,'reading new messages clears unread';
 perform public.core_notifications('device',jsonb_build_object('id',device,'proof_hash',encode(sha256(convert_to(proof,'UTF8')),'hex')));
 v:=public.core_push_snapshot(device,proof);
 assert v-array['count','id','created_at','kind','url']='{}','push capability returns only generic notification metadata';
 begin perform public.core_push_snapshot(device,repeat('x',43)); raise exception 'Wrong push proof accepted'; exception when sqlstate 'PT404' then null; end;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',host,'session_id',hs,'role','authenticated')::text,true);
 v:=public.core_chat('messages',jsonb_build_object('post_id',post));
 assert exists(select 1 from jsonb_array_elements(v->'messages') m where m->>'event_kind'='left'),'host can see exit event';
 v:=public.core_social('handoff',jsonb_build_object('post_id',post));
 assert (v->>'my_confirmed')::boolean and not (v->>'completed')::boolean,'first confirmation works before deadline but does not complete';
 perform set_config('request.jwt.claims',jsonb_build_object('sub',newcomer,'session_id',ns,'role','authenticated')::text,true);
 v:=public.core_social('handoff',jsonb_build_object('post_id',post));
 assert (v->>'completed')::boolean,'all current members complete order';
 assert (public.core_social('experience')->>'score')::int=10,'real completion adds ten points';
 perform public.core_social('handoff',jsonb_build_object('post_id',post));
 assert (public.core_social('experience')->>'score')::int=10,'retry cannot duplicate points';
 begin perform public.core_dispatch('withdraw',jsonb_build_object('post_id',post)); raise exception 'Completed order can be changed'; exception when sqlstate 'PT409' then null; end;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',first_member,'session_id',fs,'role','authenticated')::text,true);
 assert (public.core_social('experience')->>'score')::int=0,'departed member earns no completion points';
 begin perform public.core_chat('messages',jsonb_build_object('post_id',post)); raise exception 'Former member can read'; exception when sqlstate 'PT403' then null; end;
end $check$;
rollback;
