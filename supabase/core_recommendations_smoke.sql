begin;
create temporary table recommendation_fixture(user_id uuid,session_id uuid);
insert into recommendation_fixture values(gen_random_uuid(),gen_random_uuid());
insert into auth.users(id,email,email_confirmed_at) select user_id,user_id::text||'@example.test',now() from recommendation_fixture;
insert into auth.sessions(id,user_id) select session_id,user_id from recommendation_fixture;
grant select on recommendation_fixture to authenticated;
set local role authenticated;
do $test$
declare actor record; value jsonb; first_post jsonb; second_post jsonb; preferences jsonb; page jsonb; next_page jsonb;
begin
  select * into actor from recommendation_fixture;
  perform set_config('request.jwt.claims',jsonb_build_object('sub',actor.user_id,'session_id',actor.session_id,'role','authenticated')::text,true);
  perform public.core_dispatch('me');
  preferences:=public.core_engagement('preferences_get')-'version';
  preferences:=jsonb_set(preferences,'{preferred_tags}','["四小時偏好驗證"]');
  perform public.core_engagement('preferences_put',jsonb_build_object('expected_version',0,'value',preferences));
  value:=jsonb_build_object('content','這是偏好排序的隔離驗證，請勿當作真實訂單。','tags',jsonb_build_array('四小時偏好驗證'),
    'capacity',3,'deadline_at',clock_timestamp()+interval '1 hour','price_per_person',null,
    'location',jsonb_build_object('name','虛構排序測試點','latitude',24.987,'longitude',121.576));
  first_post:=public.core_dispatch('create',jsonb_build_object('value',value,'request_key','recommendation-match-post'));
  second_post:=public.core_dispatch('create',jsonb_build_object('value',jsonb_set(value,'{tags}','["其他驗證"]'),'request_key','recommendation-other-post'));
  page:=public.core_recommendation_feed('{"limit":1}');
  assert page->'items'->0->>'id'=first_post->>'id','matching preference ranks before newer nonmatch';
  assert page->>'next_cursor' is not null;
  next_page:=public.core_recommendation_feed(jsonb_build_object('limit',1,'cursor',page->>'next_cursor'));
  assert next_page->'items'->0->>'id'<>first_post->>'id','pagination does not repeat';
  perform public.core_engagement('preferences_put',jsonb_build_object('expected_version',1,'value',jsonb_set(preferences,'{preferred_tags}','["其他驗證"]')));
  begin
    perform public.core_recommendation_feed(jsonb_build_object('limit',1,'cursor',page->>'next_cursor'));
    raise exception 'cursor with old preference accepted';
  exception when sqlstate 'PT409' then null; end;
  assert public.core_recommendation_feed('{"limit":1}')->'items'->0->>'id'=second_post->>'id';
  begin
    perform public.core_recommendation_feed('{"owner_id":"forged"}');raise exception 'forged owner accepted';
  exception when sqlstate 'PT422' then null; end;
  begin
    perform public.core_recommendation_feed('{"cursor":"not-a-cursor"}');raise exception 'invalid cursor accepted';
  exception when sqlstate 'PT422' then null; end;
  assert not has_function_privilege('anon','public.core_recommendation_feed(jsonb)','EXECUTE');
end $test$;
reset role;
rollback;
