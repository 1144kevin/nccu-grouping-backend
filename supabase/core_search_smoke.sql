-- All identities, profiles, posts and messages below are rolled back.
begin;
create temporary table search_fixture(name text,user_id uuid,session_id uuid);
insert into search_fixture values('host',gen_random_uuid(),gen_random_uuid()),
  ('member',gen_random_uuid(),gen_random_uuid()),('stranger',gen_random_uuid(),gen_random_uuid());
insert into auth.users(id,email,email_confirmed_at) select user_id,user_id::text||'@example.test',now() from search_fixture;
insert into auth.sessions(id,user_id) select session_id,user_id from search_fixture;
insert into public.core_profiles(id,display_name,bio)
  select user_id,'隔離搜尋人物'||name,'共同介紹__literalsearch%' from search_fixture;
grant select on search_fixture to authenticated;
set local role authenticated;
do $test$
declare h record; m record; s record; p jsonb; j jsonb; result jsonb; next_page jsonb; q text;
begin
  select * into h from search_fixture where name='host';
  select * into m from search_fixture where name='member';
  select * into s from search_fixture where name='stranger';
  assert not has_function_privilege('anon','public.core_search(jsonb)','EXECUTE');
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  result:=public.core_search(jsonb_build_object('scope','users','q','隔離搜尋人物','limit',1));
  assert jsonb_array_length(result->'items')=1 and result->>'next_cursor' is not null;
  assert not ((result->'items'->0)?'email') and not ((result->'items'->0)?'auth_mode');
  next_page:=public.core_search(jsonb_build_object('scope','users','q','隔離搜尋人物','limit',1,'cursor',result->>'next_cursor'));
  assert next_page->'items'->0->>'id'<>result->'items'->0->>'id';
  begin
    perform public.core_search(jsonb_build_object('scope','messages','q','隔離搜尋人物','cursor',result->>'next_cursor'));
    raise exception 'cross-scope cursor accepted';
  exception when sqlstate 'PT422' then null; end;
  assert jsonb_array_length(public.core_search(jsonb_build_object('scope','users','q','__literalsearch%'))->'items')=3;
  assert jsonb_array_length(public.core_search(jsonb_build_object('scope','users','q',h.user_id::text||'@example.test'))->'items')=0;
  p:=public.core_dispatch('create',jsonb_build_object('value',jsonb_build_object(
    'content','隔離搜尋貼文：驗證私人訊息不會外洩。','tags',jsonb_build_array('隔離搜尋'),
    'capacity',3,'deadline_at',clock_timestamp()+interval '1 hour','price_per_person','90.00',
    'location',jsonb_build_object('name','虛構測試點','latitude',24.987,'longitude',121.576)),
    'request_key','search-isolated-post'));
  q:='隔離私人訊息'||h.user_id::text;
  perform public.core_chat('send',jsonb_build_object('post_id',p->>'id','content',q,'client_key','search-isolated-message'));
  assert jsonb_array_length(public.core_search(jsonb_build_object('scope','messages','q',q))->'items')=1;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',s.user_id,'session_id',s.session_id,'role','authenticated')::text,true);
  assert jsonb_array_length(public.core_search(jsonb_build_object('scope','messages','q',q))->'items')=0;
  begin
    perform public.core_search(jsonb_build_object('scope','users','q','隔離搜尋人物','cursor',result->>'next_cursor'));
    raise exception 'other account cursor accepted';
  exception when sqlstate 'PT422' then null; end;
  begin
    perform public.core_search(jsonb_build_object('scope','messages','q',q,'owner_id',h.user_id));
    raise exception 'spoofed owner accepted';
  exception when sqlstate 'PT422' then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',m.user_id,'session_id',m.session_id,'role','authenticated')::text,true);
  j:=public.core_dispatch('join',jsonb_build_object('post_id',p->>'id','expected_version',1));
  assert jsonb_array_length(public.core_search(jsonb_build_object('scope','messages','q',q))->'items')=0;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  perform public.core_dispatch('decide',jsonb_build_object('join_id',j->>'id','status','accepted'));
  perform set_config('request.jwt.claims',jsonb_build_object('sub',m.user_id,'session_id',m.session_id,'role','authenticated')::text,true);
  assert jsonb_array_length(public.core_search(jsonb_build_object('scope','messages','q',q))->'items')=1;
  perform public.core_dispatch('withdraw',jsonb_build_object('post_id',p->>'id'));
  assert jsonb_array_length(public.core_search(jsonb_build_object('scope','messages','q',q))->'items')=0;
  assert jsonb_array_length(public.core_search(jsonb_build_object('scope','history','q','隔離搜尋'))->'items')=0;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  perform public.core_dispatch('cancel',jsonb_build_object('post_id',p->>'id'));
  assert jsonb_array_length(public.core_search(jsonb_build_object('scope','history','q','隔離搜尋'))->'items')=1;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',s.user_id,'session_id',s.session_id,'role','authenticated')::text,true);
  assert jsonb_array_length(public.core_search(jsonb_build_object('scope','history','q','隔離搜尋'))->'items')=0;
end $test$;
reset role;
delete from auth.sessions where id in(select session_id from search_fixture where name='host');
set local role authenticated;
do $test$
declare h record;
begin
  select * into h from search_fixture where name='host';
  perform set_config('request.jwt.claims',jsonb_build_object('sub',h.user_id,'session_id',h.session_id,'role','authenticated')::text,true);
  begin perform public.core_search(jsonb_build_object('scope','users','q','隔離搜尋人物'));raise exception 'revoked session accepted';
  exception when sqlstate 'PT401' then null; end;
end $test$;
reset role;
rollback;
