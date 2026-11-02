begin;
create temporary table activity_fixture(name text,user_id uuid,session_id uuid);
insert into activity_fixture values('host',gen_random_uuid(),gen_random_uuid()),
  ('member',gen_random_uuid(),gen_random_uuid()),('stranger',gen_random_uuid(),gen_random_uuid());
insert into auth.users(id,email,email_confirmed_at)
  select user_id,user_id::text||'@example.test',now() from activity_fixture;
insert into auth.sessions(id,user_id) select session_id,user_id from activity_fixture;
grant select on activity_fixture to authenticated;
set local role authenticated;
do $test$
declare h record; m record; s record; p jsonb; other_post jsonb; joined jsonb; first_message jsonb; second_message jsonb; later_message jsonb;
begin
  select * into h from activity_fixture where name='host';
  select * into m from activity_fixture where name='member';
  select * into s from activity_fixture where name='stranger';
  assert not has_function_privilege('anon','public.core_chat_activity(text,jsonb)','EXECUTE'),'anonymous activity denied';
  assert not has_table_privilege('authenticated','public.core_chat_reads','SELECT'),'other viewers stay private';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  p:=public.core_dispatch('create',jsonb_build_object('value',jsonb_build_object(
    'content','已讀與未讀測試：收到訊息後可以知道真實未讀數量。','tags',jsonb_build_array('未讀測試'),
    'capacity',3,'deadline_at',now()+interval '1 day','price_per_person','50.00',
    'location',jsonb_build_object('name','虛構測試點','latitude',24.987,'longitude',121.576)), 'request_key','activity-smoke-post'));
  other_post:=public.core_dispatch('create',jsonb_build_object('value',p-array['id','author_id','author_name','author_badge','is_demo','created_at','version','joined','status','my_status','can_edit','completed'],'request_key','activity-smoke-other-post'));
  first_message:=public.core_chat('send',jsonb_build_object('post_id',p->>'id','content','第一則訊息','client_key','activity-first-message'));
  second_message:=public.core_chat('send',jsonb_build_object('post_id',other_post->>'id','content','另一個群組','client_key','activity-other-message'));
  begin
    perform public.core_chat_activity('read',jsonb_build_object('post_id',p->>'id','message_id',second_message->>'id'));
    raise exception 'cross-group read marker incorrectly allowed';
  exception when sqlstate 'PT404' then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',m.user_id,'session_id',m.session_id,'role','authenticated')::text,true);
  joined:=public.core_dispatch('join',jsonb_build_object('post_id',p->>'id','expected_version',1));
  assert (public.core_chat_activity('status')->>'total_unread')::integer=0,'pending member has no private unread count';
  begin
    perform public.core_chat_activity('read',jsonb_build_object('post_id',p->>'id','message_id',first_message->>'id'));
    raise exception 'pending member incorrectly marked private messages read';
  exception when sqlstate 'PT403' then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  assert (public.core_chat_activity('status')->>'pending_requests')::integer=1,'real pending badge';
  perform public.core_dispatch('decide',jsonb_build_object('join_id',joined->>'id','status','accepted'));
  assert (public.core_chat_activity('status')->>'pending_requests')::integer=0,'accepted clears pending badge';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',m.user_id,'session_id',m.session_id,'role','authenticated')::text,true);
  assert (public.core_chat_activity('status')->>'total_unread')::integer=1,'new member sees host message';
  second_message:=public.core_chat('send',jsonb_build_object('post_id',p->>'id','content','團員自己的訊息','client_key','activity-member-message'));
  assert (public.core_chat_activity('status')->>'total_unread')::integer=1,'own messages are not unread';
  perform public.core_chat_activity('read',jsonb_build_object('post_id',p->>'id','message_id',first_message->>'id'));
  assert (public.core_chat_activity('status')->>'total_unread')::integer=0,'displayed message clears unread';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  assert (public.core_chat_activity('status')->>'total_unread')::integer=1,'host sees member message';
  later_message:=public.core_chat('send',jsonb_build_object('post_id',p->>'id','content','後來的新訊息','client_key','activity-later-message'));
  perform set_config('request.jwt.claims',jsonb_build_object('sub',m.user_id,'session_id',m.session_id,'role','authenticated')::text,true);
  assert (public.core_chat_activity('status')->>'total_unread')::integer=1,'new message after read stays unread';
  perform public.core_chat_activity('read',jsonb_build_object('post_id',p->>'id','message_id',first_message->>'id'));
  assert (public.core_chat_activity('status')->>'total_unread')::integer=1,'marking an older displayed page cannot clear a newer arrival';
  perform public.core_chat_activity('read',jsonb_build_object('post_id',p->>'id','message_id',later_message->>'id'));
  perform public.core_chat_activity('read',jsonb_build_object('post_id',p->>'id','message_id',first_message->>'id'));
  assert (public.core_chat_activity('status')->>'total_unread')::integer=0,'delayed old read cannot move cursor backwards';
  perform public.core_dispatch('withdraw',jsonb_build_object('post_id',p->>'id'));
  assert jsonb_array_length(public.core_chat_activity('status')->'groups')=0,'withdrawn group removed from activity';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',s.user_id,'session_id',s.session_id,'role','authenticated')::text,true);
  assert (public.core_chat_activity('status')->>'pending_requests')::integer=0 and jsonb_array_length(public.core_chat_activity('status')->'groups')=0,'other account isolated';
end;
$test$;
rollback;
