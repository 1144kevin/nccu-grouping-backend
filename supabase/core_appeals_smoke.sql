-- Rollback only. No actual user or content is moderated.
do $test$
declare u uuid:=gen_random_uuid(); author uuid:=gen_random_uuid(); sid uuid:=gen_random_uuid(); author_sid uuid:=gen_random_uuid();
  report uuid:=gen_random_uuid(); post uuid:=gen_random_uuid(); message uuid:=gen_random_uuid(); appeal uuid;
  args jsonb; result jsonb; key uuid:=gen_random_uuid();
begin
  insert into auth.users(id,email,email_confirmed_at) values(u,'appeal-'||u||'@example.test',now()),(author,'appeal-'||author||'@example.test',now());
  insert into auth.sessions(id,user_id) values(sid,u),(author_sid,author);
  insert into public.core_profiles(id,display_name) values(u,'隔離回報者'),(author,'隔離作者') on conflict do nothing;
  insert into public.core_posts(id,author_id,payload,original_payload,capacity,deadline_at,request_key)
    values(post,author,'{"content":"隔離測試拼單"}','{"content":"隔離測試拼單"}',3,now()+interval '1 hour','appeal-'||post);
  insert into public.core_chat_messages(id,post_id,sender_id,content,client_key) values(message,post,author,'隔離原文不能外洩','appeal-'||message);
  insert into public.core_reports(id,owner_id,request_key,payload) values(report,u,'appeal-'||report,
    jsonb_build_object('kind','message','target_id',message,'detail','隔離回報與覆核測試內容','reason','spam','platform','web','app_version','1'));
  perform set_config('request.jwt.claims',jsonb_build_object('sub',u,'session_id',sid,'role','authenticated')::text,true);
  begin
    perform public.core_moderation_context(report);
    raise exception 'reporter read private context';
  exception when sqlstate 'PT403' then null; end;
  args:=jsonb_build_object('report_id',report,'detail','請重新檢視這筆測試回報及處理依據','request_key',key);
  begin
    perform public.core_appeals('create',args);
    raise exception 'appeal before closure';
  exception when sqlstate 'PT409' then null; end;
  insert into public.core_moderators(user_id,enabled,expires_at) values(u,true,now()+interval '1 hour');
  assert public.core_moderation_context(report)->>'content'='隔離原文不能外洩','operator context missing';
  perform public.core_moderation('review',jsonb_build_object('report_id',report,'expected_version',1,'action','review','public_response','正在查核隔離資料','internal_note','私密備註','request_key',gen_random_uuid()));
  perform public.core_moderation('review',jsonb_build_object('report_id',report,'expected_version',2,'action','hide_message','public_response','查核後已隱藏這則訊息','internal_note','私密備註','request_key',gen_random_uuid()));
  assert (select core_private.chat_message_view(m)->>'content' from public.core_chat_messages m where id=message)='此訊息已由管理者隱藏','chat content leaked';
  assert (select content from public.core_chat_messages where id=message)='隔離原文不能外洩','original evidence erased';
  result:=public.core_appeals('create',args); appeal:=(result->>'id')::uuid;
  assert public.core_appeals('create',args)=result,'appeal retry duplicated';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',author,'session_id',author_sid,'role','authenticated')::text,true);
  assert jsonb_array_length(public.core_search(jsonb_build_object('scope','messages','q','隔離原文'))->'items')=0,'hidden message leaked through search';
  result:=public.core_appeals('decisions');
  assert jsonb_array_length(result->'items')=1 and result::text not like '%私密備註%' and result::text not like '%'||u::text||'%','author disclosure leaked';
  perform public.core_appeals('create',jsonb_build_object('report_id',report,'detail','我是被處置作者，請覆核這筆處置','request_key',gen_random_uuid()));
  assert jsonb_array_length(public.core_appeals('mine')->'items')=1,'author sees reporter appeal';
  begin
    perform public.core_appeals('list'); raise exception 'ordinary author operator';
  exception when sqlstate 'PT403' then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',u,'session_id',sid,'role','authenticated')::text,true);
  args:=jsonb_build_object('appeal_id',appeal,'expected_version',1,'status','resolved','response','已重新查核，處置結果如上，未自動恢復任何成團同意','request_key',gen_random_uuid());
  result:=public.core_appeals('review',args);
  assert public.core_appeals('review',args)=result,'review retry duplicated';
  begin
    perform public.core_appeals('review',args||jsonb_build_object('request_key',gen_random_uuid())); raise exception 'stale review accepted';
  exception when sqlstate 'PT409' then null; end;
  assert (select moderation_hidden from public.core_chat_messages where id=message),'appeal silently reversed content';
  perform set_config('request.jwt.claims','{}',true);
end $test$;
select 'appeals and message moderation rollback smoke passed' result;
