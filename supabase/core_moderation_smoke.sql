-- Run inside a rollback-only transaction AFTER the prepared migration, never commit fixtures.
do $smoke$
declare u uuid:=gen_random_uuid(); session uuid:=gen_random_uuid(); report uuid:=gen_random_uuid();
  post uuid:=gen_random_uuid(); request uuid:=gen_random_uuid(); args jsonb; result jsonb;
begin
  insert into auth.users(instance_id,id,aud,role,email,email_confirmed_at,raw_app_meta_data,raw_user_meta_data)
    values('00000000-0000-0000-0000-000000000000',u,'authenticated','authenticated',
      'moderation-rollback-'||u::text||'@example.test',now(),'{"provider":"email","providers":["email"]}',
      '{"display_name":"隔離查核測試","role":"admin"}');
  insert into auth.sessions(id,user_id,created_at,updated_at) values(session,u,now(),now());
  insert into public.core_profiles(id,display_name) values(u,'隔離查核測試') on conflict do nothing;
  insert into public.core_posts(id,author_id,payload,original_payload,capacity,deadline_at,request_key)
    values(post,u,'{"content":"隔離查核拼單，沒有真實付款"}','{"content":"隔離查核拼單，沒有真實付款"}',3,now()+interval '1 hour','fixture-'||post::text);
  insert into public.core_reports(id,owner_id,request_key,payload)
    values(report,u,'fixture-'||report::text,jsonb_build_object('kind','post','target_id',post,'reason','spam',
      'detail','隔離查核回報，沒有真人內容','platform','web','app_version','1.0.0'));
  perform set_config('request.jwt.claims',jsonb_build_object('sub',u,'role','authenticated','session_id',session)::text,true);
  if (public.core_moderation_access()->>'can_review')::boolean then
    raise exception 'Ordinary user has operator capability'; end if;
  begin
    perform public.core_moderation('list','{}');
    raise exception 'Expected denial for user_metadata admin';
  exception when sqlstate 'PT403' then null; end;
  result:=public.core_moderation('mine','{}');
  if jsonb_array_length(result->'items')<>1 or result::text like '%internal_note%' then
    raise exception 'Private review data leaked'; end if;
  -- Temporary operator exists only in this uncommitted, rollback-only transaction.
  insert into public.core_moderators(user_id,enabled,expires_at) values(u,true,now()+interval '1 hour');
  if not (public.core_moderation_access()->>'can_review')::boolean then
    raise exception 'Explicit operator missing capability'; end if;
  args:=jsonb_build_object('report_id',report,'expected_version',1,'action','review',
    'public_response','已開始查核，待核對內容。','internal_note','僅測試管理者可讀','request_key',request);
  result:=public.core_moderation('review',args);
  if result->>'status'<>'reviewing' or result->>'review_version'<>'2' then raise exception 'Review failed'; end if;
  if public.core_moderation('review',args)<>result then raise exception 'Retry not idempotent'; end if;
  begin
    perform public.core_moderation('review',args||jsonb_build_object('request_key',gen_random_uuid()));
    raise exception 'Expected stale version denial';
  exception when sqlstate 'PT409' then null; end;
  args:=args||jsonb_build_object('expected_version',2,'action','cancel_post','request_key',gen_random_uuid(),
    'public_response','查核後已取消該筆測試拼單。');
  result:=public.core_moderation('review',args);
  if result->>'status'<>'resolved' or not (select cancelled from public.core_posts where id=post) then
    raise exception 'Cancellation not atomic'; end if;
  if (select count(*) from public.core_moderation_events where report_id=report)<>2 then
    raise exception 'Audit count incorrect'; end if;
  result:=public.core_moderation('mine','{}');
  if result::text like '%僅測試管理者可讀%' or result#>>'{items,0,status}'<>'resolved' then
    raise exception 'Reporter response incorrect'; end if;
  update public.core_moderators set expires_at=now()-interval '1 second' where user_id=u;
  if (public.core_moderation_access()->>'can_review')::boolean then
    raise exception 'Expired operator retains capability'; end if;
  begin
    perform public.core_moderation('list','{}');
    raise exception 'Expected expired operator denial';
  exception when sqlstate 'PT403' then null; end;
  perform set_config('request.jwt.claims','{}',true);
end $smoke$;
select 'moderation rollback smoke passed' as result;
