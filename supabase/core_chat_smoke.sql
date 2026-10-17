-- Real database chat security/ordering tests with transaction-only fixtures.
begin;
create temporary table chat_fixture(name text,user_id uuid,session_id uuid,post_id uuid);
insert into chat_fixture(name,user_id,session_id) values ('host',gen_random_uuid(),gen_random_uuid()),
  ('member',gen_random_uuid(),gen_random_uuid()),('stranger',gen_random_uuid(),gen_random_uuid());
insert into auth.users(id,email,email_confirmed_at)
  select user_id,user_id::text||'@example.test',now() from chat_fixture;
insert into auth.sessions(id,user_id) select session_id,user_id from chat_fixture;
grant select,update on chat_fixture to authenticated;
set local role authenticated;
do $test$
declare h record; m record; s record; p jsonb; joined jsonb; sent jsonb; replay jsonb; result jsonb;
begin
  select * into h from chat_fixture where name='host';
  select * into m from chat_fixture where name='member';
  select * into s from chat_fixture where name='stranger';
  assert not has_function_privilege('anon','public.core_chat(text,jsonb)','EXECUTE'),'anonymous chat denied';
  assert not has_table_privilege('authenticated','public.core_chat_messages','SELECT'),'no direct private text reads';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  p:=public.core_dispatch('create',jsonb_build_object('value',jsonb_build_object(
    'content','聊天室交易測試：已加入的團員可以協調取餐。','tags',jsonb_build_array('聊天測試'),
    'capacity',3,'deadline_at',now()+interval '1 day','price_per_person','50.00',
    'location',jsonb_build_object('name','虛構測試點','latitude',24.987,'longitude',121.576)), 'request_key','chat-smoke-create-key'));
  update chat_fixture set post_id=(p->>'id')::uuid where name='host';
  sent:=public.core_chat('send',jsonb_build_object('post_id',p->>'id','content','  六點在校門口集合。  ','client_key','chat-smoke-send-key'));
  replay:=public.core_chat('send',jsonb_build_object('post_id',p->>'id','content','六點在校門口集合。','client_key','chat-smoke-send-key'));
  assert sent->>'id'=replay->>'id','send retry does not duplicate messages';
  assert sent->>'sender_id'=h.user_id::text and not (sent?'client_key'),'sender authority and private key';
  assert public.core_chat('list')->'items'->0->'last_message'->>'id'=sent->>'id','list preview';
  begin
    perform public.core_chat('send',jsonb_build_object('post_id',p->>'id','content','不同內容','client_key','chat-smoke-send-key'));
    raise exception 'conflicting retry incorrectly allowed';
  exception when sqlstate 'PT409' then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',m.user_id,'session_id',m.session_id,'role','authenticated')::text,true);
  joined:=public.core_dispatch('join',jsonb_build_object('post_id',p->>'id','expected_version',1));
  begin
    perform public.core_chat('messages',jsonb_build_object('post_id',p->>'id'));
    raise exception 'pending member incorrectly read chat';
  exception when sqlstate 'PT403' then null; end;
  assert jsonb_array_length(public.core_chat('list')->'items')=0,'pending member has no group';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  perform public.core_dispatch('decide',jsonb_build_object('join_id',joined->>'id','status','accepted'));
  perform set_config('request.jwt.claims',jsonb_build_object('sub',m.user_id,'session_id',m.session_id,'role','authenticated')::text,true);
  result:=public.core_chat('messages',jsonb_build_object('post_id',p->>'id'));
  assert jsonb_array_length(result->'messages')=1 and (result->>'can_send')::boolean,'accepted member reads chat';
  perform public.core_chat('send',jsonb_build_object('post_id',p->>'id','content','收到，謝謝！','client_key','chat-member-send-key'));
  perform public.core_dispatch('withdraw',jsonb_build_object('post_id',p->>'id'));
  begin
    perform public.core_chat('messages',jsonb_build_object('post_id',p->>'id'));
    raise exception 'withdrawn member incorrectly read new messages';
  exception when sqlstate 'PT403' then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',s.user_id,'session_id',s.session_id,'role','authenticated')::text,true);
  begin
    perform public.core_chat('send',jsonb_build_object('post_id',p->>'id','content','冒用','client_key','chat-stranger-key'));
    raise exception 'stranger incorrectly sent message';
  exception when sqlstate 'PT403' then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  perform public.core_dispatch('cancel',jsonb_build_object('post_id',p->>'id'));
  assert not (public.core_chat('messages',jsonb_build_object('post_id',p->>'id'))->>'can_send')::boolean,'cancelled chat is read-only';
  begin
    perform public.core_chat('send',jsonb_build_object('post_id',p->>'id','content','取消後發訊息','client_key','chat-cancelled-key'));
    raise exception 'cancelled chat incorrectly accepted new message';
  exception when sqlstate 'PT409' then null; end;
end;
$test$;
reset role;
-- Trusted fixture insertion supplies enough rows for deterministic pagination/quota checks.
insert into public.core_chat_messages(post_id,sender_id,content,client_key,created_at)
  select f.post_id,f.user_id,'排序測試 '||n,'chat-pagination-'||n,clock_timestamp()+n*interval '1 millisecond'
  from chat_fixture f cross join generate_series(1,31) n where f.name='host';
update public.core_posts set cancelled=false where id=(select post_id from chat_fixture where name='host');
set local role authenticated;
do $test$
declare h record; page jsonb; older jsonb;
begin
  select * into h from chat_fixture where name='host';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  page:=public.core_chat('messages',jsonb_build_object('post_id',h.post_id,'limit',30));
  assert jsonb_array_length(page->'messages')=30 and page->>'next_cursor' is not null,'message page limit';
  older:=public.core_chat('messages',jsonb_build_object('post_id',h.post_id,'limit',30,'cursor',page->>'next_cursor'));
  assert jsonb_array_length(older->'messages')=3 and older->>'next_cursor' is null,'older messages preserved';
  assert (older->'messages'->2->>'created_at')::timestamptz<=(page->'messages'->0->>'created_at')::timestamptz,'chronological pages';
  begin
    perform public.core_chat('send',jsonb_build_object('post_id',h.post_id,'content','超過每分鐘上限','client_key','chat-quota-overflow'));
    raise exception 'chat quota incorrectly allowed';
  exception when sqlstate 'PT429' then null; end;
  begin
    perform public.core_chat('messages',jsonb_build_object('post_id',h.post_id,'actor','forged'));
    raise exception 'actor injection incorrectly allowed';
  exception when sqlstate 'PT422' then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',gen_random_uuid(),'role','authenticated')::text,true);
  begin
    perform public.core_chat('list'); raise exception 'revoked chat session incorrectly allowed';
  exception when sqlstate 'PT401' then null; end;
end;
$test$;
rollback;
