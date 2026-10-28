-- Rollback-only: prove both image RPCs pass the old owner and project limits.
begin;
create temporary table unlimited_media_fixture(user_id uuid,session_id uuid);
insert into unlimited_media_fixture values(gen_random_uuid(),gen_random_uuid());
insert into auth.users(id,email,email_confirmed_at)
  select user_id,user_id::text||'@unlimited-media.invalid',now() from unlimited_media_fixture;
insert into auth.sessions(id,user_id) select session_id,user_id from unlimited_media_fixture;
grant select on unlimited_media_fixture to authenticated;
set local role authenticated;
do $test$
declare actor record; request jsonb; first_post jsonb; first_chat jsonb; i integer;
begin
  select * into actor from unlimited_media_fixture;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',actor.user_id,'session_id',actor.session_id,'role','authenticated')::text,true);
  perform public.core_dispatch('me');
  request:=jsonb_build_object('sha256',repeat('0',64),'width',480,'height',320);
  for i in 1..101 loop
    perform public.core_media('reserve',request||jsonb_build_object('request_key','unlimited-post-photo-'||i));
    perform public.core_chat_media('reserve',request||jsonb_build_object('request_key','unlimited-chat-photo-'||i));
  end loop;
  assert jsonb_array_length(public.core_media('list')->'items')=101;
  assert jsonb_array_length(public.core_chat_media('list')->'items')=101;
  first_post:=public.core_media('reserve',request||jsonb_build_object('request_key','unlimited-post-photo-1'));
  first_chat:=public.core_chat_media('reserve',request||jsonb_build_object('request_key','unlimited-chat-photo-1'));
  assert jsonb_array_length(public.core_media('list')->'items')=101;
  assert jsonb_array_length(public.core_chat_media('list')->'items')=101;
  begin
    perform public.core_media('reserve',request||jsonb_build_object('request_key','unlimited-post-photo-1','width',481));
    raise exception 'changed-input retry accepted';
  exception when sqlstate 'PT409' then null; end;
  begin
    perform public.core_chat_media('read',jsonb_build_object('image_id',first_chat->>'id'));
    raise exception 'unverified chat image readable';
  exception when sqlstate 'PT409' then null; end;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',actor.user_id,'session_id',gen_random_uuid(),'role','authenticated')::text,true);
  begin
    perform public.core_media('reserve',request||jsonb_build_object('request_key','invalid-session-photo'));
    raise exception 'invalid session reserved image';
  exception when sqlstate 'PT401' then null; end;
end $test$;
reset role;
rollback;
select 'passed: 101 post + 101 chat reservations; idempotency/session/verification intact; all fixtures rolled back' as result;
