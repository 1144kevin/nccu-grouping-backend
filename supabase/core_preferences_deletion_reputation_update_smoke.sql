-- Synthetic identities only; all changes are rolled back.
begin;
do $test$
declare actor uuid:=gen_random_uuid(); session_id uuid:=gen_random_uuid(); v jsonb; result jsonb;
begin
 insert into auth.users(id,email,email_confirmed_at) values(actor,actor::text||'@example.invalid',now());
 insert into auth.sessions(id,user_id,created_at) values(session_id,actor,now()-interval '1 day');
 perform set_config('request.jwt.claims',jsonb_build_object('sub',actor,'session_id',session_id,'role','authenticated')::text,true);
 perform public.core_dispatch('me');
 assert core_private.reputation_score(actor)=50, 'new account starts at 50';
 v:=public.core_engagement('preferences_get');
 v:=(v-'version')||jsonb_build_object('preferred_tags',jsonb_build_array('測試宿舍','測試商品'),'custom_locations',jsonb_build_array('測試宿舍'));
 result:=public.core_engagement('preferences_put',jsonb_build_object('expected_version',0,'value',v));
 assert result->'custom_locations'='["測試宿舍"]'::jsonb, 'custom location saved';
 result:=public.core_engagement('preferences_put',jsonb_build_object('expected_version',1,'value',v-'custom_locations'));
 assert result->'custom_locations'='["測試宿舍"]'::jsonb, 'older clients preserve classification';
 result:=public.core_engagement('preferences_put',jsonb_build_object('expected_version',1,'value',jsonb_set(v-'custom_locations','{preferred_tags}','["測試商品"]')));
 assert result->'custom_locations'='[]'::jsonb, 'older client can remove location';
 begin
  perform public.core_engagement('preferences_put',jsonb_build_object('expected_version',2,'value',jsonb_set(v,'{custom_locations}','["其他地點"]')));
  raise exception 'invalid location was accepted';
 exception when sqlstate 'PT422' then null; end;
 assert (public.core_account_deletion('status')->>'recent_auth')::boolean, 'older active session accepted';
 result:=public.core_account_deletion('prepare');
 begin
  perform public.core_account_deletion('begin',jsonb_build_object('confirmation','wrong','challenge',result->>'challenge'));
  raise exception 'wrong confirmation was accepted';
 exception when sqlstate 'PT422' then null; end;
 assert public.core_account_deletion('begin',jsonb_build_object('confirmation','DELETE','challenge',result->>'challenge'))->>'phase'='deleting', 'confirmation accepted';
end $test$;
rollback;
