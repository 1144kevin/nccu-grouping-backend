-- Disposable transaction-only identities; no email, permanent fixture or human data changes.
begin;
create temporary table engagement_fixture(name text,user_id uuid,session_id uuid);
insert into engagement_fixture values('host',gen_random_uuid(),gen_random_uuid()),
  ('member',gen_random_uuid(),gen_random_uuid()),('stranger',gen_random_uuid(),gen_random_uuid());
insert into auth.users(id,email,email_confirmed_at)
  select user_id,user_id::text||'@example.test',now() from engagement_fixture;
insert into auth.sessions(id,user_id) select session_id,user_id from engagement_fixture;
grant select on engagement_fixture to authenticated;
set local role authenticated;
do $test$
declare h record; m record; s record; prefs jsonb; updated jsonb; p jsonb; joined jsonb;
  message jsonb; report jsonb; value jsonb; item jsonb; i integer;
begin
  select * into h from engagement_fixture where name='host';
  select * into m from engagement_fixture where name='member';
  select * into s from engagement_fixture where name='stranger';
  assert not has_function_privilege('anon','public.core_engagement(text,jsonb)','EXECUTE');
  assert not has_table_privilege('authenticated','public.core_reports','SELECT');
  assert not has_table_privilege('authenticated','public.core_account_preferences','UPDATE');
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  perform public.core_dispatch('me');
  prefs:=public.core_engagement('preferences_get');
  assert prefs->>'version'='0' and prefs->'preferred_tags'='[]';
  value:=jsonb_set(prefs-'version','{preferred_tags}','["披薩","晚餐"]');
  updated:=public.core_engagement('preferences_put',jsonb_build_object('expected_version',0,'value',value));
  assert updated->>'version'='1' and updated->'preferred_tags'='["披薩","晚餐"]';
  assert public.core_engagement('preferences_put',jsonb_build_object('expected_version',1,'value',value))=updated;
  begin
    perform public.core_engagement('preferences_put',jsonb_build_object('expected_version',0,'value',value));
    raise exception 'stale preferences accepted';
  exception when sqlstate 'PT409' then null; end;
  begin
    perform public.core_engagement('preferences_put',jsonb_build_object('expected_version',1,
      'value',jsonb_set(value,'{notifications,messages}','"false"')));
    raise exception 'string boolean accepted';
  exception when sqlstate 'PT422' then null; end;
  begin
    perform public.core_engagement('preferences_get',jsonb_build_object('owner_id',m.user_id));
    raise exception 'forged owner accepted';
  exception when sqlstate 'PT422' then null; end;
  begin
    perform public.core_engagement('preferences_put',jsonb_build_object('expected_version',1,
      'value',jsonb_set(value,'{preferred_tags}',jsonb_build_array(repeat(' ',100)||'x'))));
    raise exception 'oversized whitespace tag accepted';
  exception when sqlstate 'PT422' then null; end;
  p:=public.core_dispatch('create',jsonb_build_object('value',jsonb_build_object(
    'content','隔離驗證：聊天室偏好與私人回報的交易測試。','tags',jsonb_build_array('驗證'),
    'capacity',3,'deadline_at',clock_timestamp()+interval '1 hour','price_per_person','90.00',
    'location',jsonb_build_object('name','虛構測試點','latitude',24.987,'longitude',121.576)),
    'request_key','engagement-smoke-post'));
  message:=public.core_chat('send',jsonb_build_object('post_id',p->>'id','content','交易內私人訊息測試','client_key','engagement-smoke-message'));
  value:=jsonb_build_object('kind','feedback','target_id',null,'reason','other',
    'detail','這是隔離回報驗證，交易結束即回滾。','platform','web','app_version','test');
  report:=public.core_engagement('report',jsonb_build_object('request_key','engagement-smoke-report','value',value));
  assert report->>'status'='received';
  assert public.core_engagement('report',jsonb_build_object('request_key','engagement-smoke-report','value',value))=report;
  begin
    perform public.core_engagement('report',jsonb_build_object('request_key','engagement-smoke-report',
      'value',jsonb_set(value,'{detail}','"同一請求不同內容，必須拒絕，不能覆蓋。"')));
    raise exception 'conflicting report replay accepted';
  exception when sqlstate 'PT409' then null; end;
  assert jsonb_array_length(public.core_engagement('reports')->'items')=1;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',s.user_id,'session_id',s.session_id,'role','authenticated')::text,true);
  perform public.core_dispatch('me');
  assert jsonb_array_length(public.core_engagement('reports')->'items')=0;
  assert public.core_engagement('preferences_get')->>'version'='0';
  begin
    perform public.core_engagement('inbox_put',jsonb_build_object('post_id',p->>'id','muted',true,'archived',true));
    raise exception 'foreign chat settings accepted';
  exception when sqlstate 'PT403' then null; end;
  begin
    perform public.core_engagement('report',jsonb_build_object('request_key','engagement-foreign-message',
      'value',value||jsonb_build_object('kind','message','target_id',message->>'id')));
    raise exception 'foreign private message reported';
  exception when sqlstate 'PT404' then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',m.user_id,'session_id',m.session_id,'role','authenticated')::text,true);
  joined:=public.core_dispatch('join',jsonb_build_object('post_id',p->>'id','expected_version',1));
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  perform public.core_dispatch('decide',jsonb_build_object('join_id',joined->>'id','status','accepted'));
  perform set_config('request.jwt.claims',jsonb_build_object('sub',m.user_id,'session_id',m.session_id,'role','authenticated')::text,true);
  item:=public.core_engagement('inbox_put',jsonb_build_object('post_id',p->>'id','muted',true,'archived',true));
  assert item->>'muted'='true' and item->>'archived'='true';
  assert jsonb_array_length(public.core_engagement('inbox_list')->'items')=1;
  assert (public.core_chat('messages',jsonb_build_object('post_id',p->>'id','limit',30))->>'can_send')::boolean;
  perform public.core_dispatch('withdraw',jsonb_build_object('post_id',p->>'id'));
  assert jsonb_array_length(public.core_engagement('inbox_list')->'items')=0;
  begin
    perform public.core_engagement('inbox_put',jsonb_build_object('post_id',p->>'id','muted',false,'archived',false));
    raise exception 'withdrawn member changed private chat preferences';
  exception when sqlstate 'PT403' then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  for i in 1..4 loop
    perform public.core_engagement('report',jsonb_build_object('request_key','engagement-quota-'||i,'value',value));
  end loop;
  begin
    perform public.core_engagement('report',jsonb_build_object('request_key','engagement-quota-six','value',value));
    raise exception 'report limit bypassed';
  exception when sqlstate 'PT429' then null; end;
  assert public.core_engagement('report',jsonb_build_object('request_key','engagement-smoke-report','value',value))=report;
end $test$;
reset role;
delete from auth.sessions where id in(select session_id from engagement_fixture where name='host');
set local role authenticated;
do $test$
declare h record;
begin
  select * into h from engagement_fixture where name='host';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  begin
    perform public.core_engagement('preferences_get');raise exception 'revoked session accepted';
  exception when sqlstate 'PT401' then null; end;
end $test$;
reset role;
rollback;
