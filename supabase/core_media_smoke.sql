-- Transaction-only DB test: no real Storage objects, emails or persistent accounts.
begin;
create temporary table media_fixture(name text,user_id uuid,session_id uuid);
insert into media_fixture values('a',gen_random_uuid(),gen_random_uuid()),('b',gen_random_uuid(),gen_random_uuid());
insert into auth.users(id,email,email_confirmed_at) select user_id,user_id::text||'@example.test',now() from media_fixture;
insert into auth.sessions(id,user_id) select session_id,user_id from media_fixture;
grant select on media_fixture to authenticated;
set local role authenticated;
do $test$
declare a record; b record; asset jsonb; again jsonb; request jsonb; i integer;
begin
 select * into a from media_fixture where name='a'; select * into b from media_fixture where name='b';
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a.user_id,'session_id',a.session_id,'role','authenticated')::text,true);
 perform public.core_dispatch('me');
 request:=jsonb_build_object('request_key','media-transaction-idempotency','sha256',repeat('0',64),'width',480,'height',320);
 asset:=public.core_media('reserve',request);
 again:=public.core_media('reserve',request);
 assert asset->>'id'=again->>'id';
 begin
  perform public.core_media('reserve',request||jsonb_build_object('width',481)); raise exception 'same key with changed input accepted';
 exception when sqlstate 'PT409' then null; end;
 begin
  perform public.core_media('reserve',request||jsonb_build_object('owner_id',b.user_id)); raise exception 'forged owner accepted';
 exception when sqlstate 'PT422' then null; end;
 begin
  perform public.core_media('read',jsonb_build_object('image_id',asset->>'id')); raise exception 'pending image readable';
 exception when sqlstate 'PT409' then null; end;
 assert jsonb_array_length(public.core_media('list')->'items')=1;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',b.user_id,'session_id',b.session_id,'role','authenticated')::text,true);
 perform public.core_dispatch('me');
 assert jsonb_array_length(public.core_media('list')->'items')=0;
 begin
  perform public.core_media('delete_prepare',jsonb_build_object('image_id',asset->>'id')); raise exception 'stranger deleted reservation';
 exception when sqlstate 'PT404' then null; end;
 assert not core_private.media_access('select',a.user_id::text||'/'||(asset->>'id')||'.jpg');
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a.user_id,'session_id',a.session_id,'role','authenticated')::text,true);
 for i in 2..21 loop
  perform public.core_media('reserve',request||jsonb_build_object('request_key','media-transaction-quota-'||i));
 end loop;
 assert jsonb_array_length(public.core_media('list')->'items')=21;
 -- Count limits are removed; retries and reservation deletion still work.
 assert public.core_media('reserve',request)->>'id'=asset->>'id';
 assert (public.core_media('delete_finish',jsonb_build_object('image_id',asset->>'id'))->>'deleted')::boolean;
 assert jsonb_array_length(public.core_media('list')->'items')=20;
 assert not has_function_privilege('anon','public.core_media(text,jsonb)','EXECUTE');
 assert not has_table_privilege('authenticated','public.core_media_assets','SELECT');
 assert not has_table_privilege('authenticated','core_private.media_signing_keys','SELECT');
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a.user_id,'session_id',gen_random_uuid(),'role','authenticated')::text,true);
 begin
  perform public.core_media('list'); raise exception 'invalid session accepted';
 exception when sqlstate 'PT401' then null; end;
end $test$;
reset role;
rollback;
