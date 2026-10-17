-- The same database clock used by deadline validation and status calculation.
create function core_private.server_clock() returns jsonb
language plpgsql security definer set search_path = '' as $$
declare actor uuid:=auth.uid();
begin
  if actor is null or not exists(select 1 from auth.sessions where user_id=actor
    and id=(auth.jwt()->>'session_id')::uuid and (not_after is null or not_after>clock_timestamp()))
  then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  return jsonb_build_object('server_time',clock_timestamp());
end;
$$;
revoke all on function core_private.server_clock() from public,anon,authenticated;
grant execute on function core_private.server_clock() to authenticated;
create function public.core_server_clock() returns jsonb
language sql security invoker set search_path = '' as $$ select core_private.server_clock(); $$;
revoke all on function public.core_server_clock() from public,anon,authenticated;
grant execute on function public.core_server_clock() to authenticated;
