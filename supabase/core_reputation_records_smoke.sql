begin;
insert into auth.users(id,email,email_confirmed_at) values
 ('10000000-0000-4000-8000-000000000001','host@social.invalid',now()),
 ('10000000-0000-4000-8000-000000000002','guest@social.invalid',now()),
 ('10000000-0000-4000-8000-000000000003','outsider@social.invalid',now());
insert into auth.sessions(id,user_id) select id,id from auth.users where email like '%@social.invalid';
insert into public.core_profiles(id,display_name) select id,split_part(email,'@',1) from auth.users where email like '%@social.invalid';
insert into public.core_posts(id,author_id,payload,original_payload,deadline_at,capacity,request_key) values
 ('20000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000001','{"content":"一起訂晚餐，正門領取","tags":["晚餐"],"location":{"name":"正門","latitude":25,"longitude":121},"deadline_at":"2099-01-01T00:00:00Z","capacity":3,"price_per_person":"90"}','{}','2099-01-01',3,'social-fixture-post-1'),
 ('20000000-0000-4000-8000-000000000002','10000000-0000-4000-8000-000000000001','{"content":"另一筆餐點，主揪在此","tags":["晚餐"],"location":{"name":"正門","latitude":25,"longitude":121},"deadline_at":"2099-01-01T00:00:00Z","capacity":3,"price_per_person":"90"}','{}','2099-01-01',3,'social-fixture-post-2');
set local role authenticated;
do $$
declare a text:='10000000-0000-4000-8000-000000000001'; b text:='10000000-0000-4000-8000-000000000002'; stranger text:='10000000-0000-4000-8000-000000000003';
 post text:='20000000-0000-4000-8000-000000000001'; room jsonb; m jsonb; result jsonb; cur text;
begin
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a,'session_id',a)::text,true);
 begin perform public.core_social('start',jsonb_build_object('post_id',post)); raise exception 'self DM allowed'; exception when sqlstate 'PT403' then null; end;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',b,'session_id',b)::text,true);
 room:=public.core_social('start',jsonb_build_object('post_id',post));
 assert room->>'kind'='direct';
 assert public.core_social('start',jsonb_build_object('post_id',post))->>'id'=room->>'id','duplicate DM thread';
 m:=public.core_social('send',jsonb_build_object('thread_id',room->>'id','content','請問還有名額嗎？','client_key','social-fixture-message-1'));
 assert public.core_social('send',jsonb_build_object('thread_id',room->>'id','content','請問還有名額嗎？','client_key','social-fixture-message-1'))->>'id'=m->>'id','duplicate DM send';
 begin perform public.core_social('send',jsonb_build_object('thread_id',room->>'id','content','不同內容','client_key','social-fixture-message-1')); raise exception 'payload conflict allowed'; exception when sqlstate 'PT409' then null; end;
 perform public.core_social('preference',jsonb_build_object('kind','direct','thread_id',room->>'id','pinned',true,'inbox','primary','deleted',true));
 assert jsonb_array_length(public.core_social('list','{"kind":"direct"}')->'items')=0,'deleted still in inbox';
 assert jsonb_array_length(public.core_social('list','{"kind":"direct","deleted":true}')->'items')=1,'cannot recover deletion';
 perform public.core_social('preference',jsonb_build_object('kind','direct','thread_id',room->>'id','deleted',false));
 assert jsonb_array_length(public.core_social('messages',jsonb_build_object('thread_id',room->>'id'))->'messages')=1,'delete erased messages';
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a,'session_id',a)::text,true);
 result:=public.core_social('list','{}'); assert (result->>'total_unread')::int=1,'host missing unread';
 assert not ((public.core_social('messages',jsonb_build_object('thread_id',room->>'id'))->'thread'->>'pinned')::boolean),'preferences leaked across users';
 perform public.core_social('read',jsonb_build_object('thread_id',room->>'id','message_id',m->>'id'));
 assert (public.core_social('list','{}')->>'total_unread')::int=0,'read did not clear unread';
 result:=public.core_social('list','{"limit":1}');
 assert jsonb_typeof(result->'next_cursor')='string','inbox cursor must be a JSON string';
 cur:=result->>'next_cursor'; assert cur is not null,'missing inbox cursor';
 m:=public.core_social('send',jsonb_build_object('thread_id',room->>'id','content','正門取餐沒問題，加入後一起確認。','client_key','social-fixture-message-reply'));
 result:=public.core_social('messages',jsonb_build_object('thread_id',room->>'id','limit',1));
 assert jsonb_typeof(result->'next_cursor')='string','message cursor must be a JSON string';
 result:=public.core_social('messages',jsonb_build_object('thread_id',room->>'id','limit',1,'cursor',result->>'next_cursor'));
 assert jsonb_array_length(result->'messages')=1 and result->'messages'->0->>'id'<>m->>'id','message pagination repeated';
 perform set_config('request.jwt.claims',jsonb_build_object('sub',stranger,'session_id',stranger)::text,true);
 begin perform public.core_social('messages',jsonb_build_object('thread_id',room->>'id')); raise exception 'outsider read'; exception when sqlstate 'PT404' then null; end;
 begin perform public.core_social('send',jsonb_build_object('thread_id',room->>'id','content','不應傳送','client_key','outsider-invalid-message')); raise exception 'outsider send'; exception when sqlstate 'PT404' then null; end;
 begin perform public.core_social('preference',jsonb_build_object('kind','direct','thread_id',room->>'id','pinned',true)); raise exception 'outsider preference'; exception when sqlstate 'PT404' then null; end;
 begin perform public.core_social('list',jsonb_build_object('limit',1,'cursor',cur)); raise exception 'cursor crossed users'; exception when sqlstate 'PT422' then null; end;
 begin perform 1 from public.core_direct_messages; raise exception 'direct table exposed'; exception when insufficient_privilege then null; end;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',b,'session_id',a)::text,true);
 begin perform public.core_social('list','{}'); raise exception 'foreign session allowed'; exception when sqlstate 'PT401' then null; end;
end $$;
reset role;
insert into public.core_user_blocks(owner_id,blocked_id) values('10000000-0000-4000-8000-000000000002','10000000-0000-4000-8000-000000000001');
set local role authenticated;
select set_config('request.jwt.claims','{"sub":"10000000-0000-4000-8000-000000000002","session_id":"10000000-0000-4000-8000-000000000002"}',true);
do $$ begin
 assert jsonb_array_length(public.core_social('list','{"kind":"direct"}')->'items')=0,'blocked DM listed';
 begin perform public.core_social('start','{"post_id":"20000000-0000-4000-8000-000000000001"}'); raise exception 'blocked DM opened'; exception when sqlstate 'PT403' or sqlstate 'PT404' then null; end;
end $$;
reset role;
delete from public.core_user_blocks where owner_id='10000000-0000-4000-8000-000000000002';
update public.core_posts set deadline_at=clock_timestamp()-interval '1 hour' where id='20000000-0000-4000-8000-000000000001';
insert into public.core_join_requests(post_id,requester_id,status) values('20000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000002','accepted');
set local role authenticated;
do $$ declare post text:='20000000-0000-4000-8000-000000000001'; a text:='10000000-0000-4000-8000-000000000001'; b text:='10000000-0000-4000-8000-000000000002'; begin
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a,'session_id',a)::text,true);
 assert not (public.core_social('handoff',jsonb_build_object('post_id',post))->>'completed')::boolean,'one person completed group';
 assert (public.core_social('experience','{}')->>'score')::int=0,'unconfirmed score';
 perform set_config('request.jwt.claims',jsonb_build_object('sub',b,'session_id',b)::text,true);
 assert (public.core_social('handoff',jsonb_build_object('post_id',post))->>'completed')::boolean,'all receipts did not complete';
 assert (public.core_social('experience','{}')->>'score')::int=10,'wrong earned score';
 perform public.core_social('handoff',jsonb_build_object('post_id',post));
 assert (public.core_social('experience','{}')->>'score')::int=10,'retry earned duplicate score';
end $$;

reset role;
insert into public.core_posts(id,author_id,payload,original_payload,deadline_at,capacity,request_key)
select v.id,'10000000-0000-4000-8000-000000000001',p.payload,p.original_payload,clock_timestamp()-interval '1 hour',3,v.key
from public.core_posts p cross join (values('20000000-0000-4000-8000-000000000003'::uuid,'reputation-fixture-extra-3'),('20000000-0000-4000-8000-000000000004'::uuid,'reputation-fixture-extra-4')) v(id,key)
where p.id='20000000-0000-4000-8000-000000000001';
insert into public.core_join_requests(post_id,requester_id,status)
select id,'10000000-0000-4000-8000-000000000002','accepted' from public.core_posts where id in('20000000-0000-4000-8000-000000000003','20000000-0000-4000-8000-000000000004');
insert into public.core_handoff_receipts(post_id,member_id)
select p.id,u.id from public.core_posts p cross join public.core_profiles u
where p.id in('20000000-0000-4000-8000-000000000003','20000000-0000-4000-8000-000000000004') and u.id in('10000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000002');
set local role authenticated;

do $$ declare value jsonb; begin
 perform set_config('request.jwt.claims','{"sub":"10000000-0000-4000-8000-000000000001","session_id":"10000000-0000-4000-8000-000000000001"}',true);
 value:=public.core_social('experience','{}');
 assert value->>'score'='30' and value->>'level'='2','verified completion tier mismatch';
 value:=public.core_order_history('{"limit":1}');
 assert jsonb_array_length(value->'items')=1 and value->>'next_cursor' is not null,'account records must paginate active and completed orders';
 assert value->'items'->0->>'author_level'='2','post level not exposed';
 value:=public.core_order_history(jsonb_build_object('limit',1,'cursor',value->>'next_cursor'));
 assert jsonb_array_length(value->'items')=1,'account record next page lost item';
 value:=public.core_public_user('profile','{"user_id":"10000000-0000-4000-8000-000000000002"}');
 assert value->>'level'='2','public reputation mismatch';
 assert not has_function_privilege('authenticated','core_private.reputation_level(uuid)','EXECUTE'),'private scoring helper exposed';
 value:=public.core_search('{"scope":"users","q":"guest"}');
 assert value->'items'->0->>'level'='2','search reputation omitted';
end $$;
reset role;
update public.core_posts set demo_batch='reputation-smoke',demo_owner_id=author_id where id='20000000-0000-4000-8000-000000000002';
set local role authenticated;
do $$ declare value jsonb; begin
 perform set_config('request.jwt.claims','{"sub":"10000000-0000-4000-8000-000000000003","session_id":"10000000-0000-4000-8000-000000000003"}',true);
 value:=public.core_social('start','{"post_id":"20000000-0000-4000-8000-000000000002"}');
 assert value->>'kind'='direct','demo inquiry must be available';
 assert value->>'peer_level'='2','peer reputation omitted';
end $$;
rollback;
select 'social, reputation, account pagination and demo inquiries passed' as result;
