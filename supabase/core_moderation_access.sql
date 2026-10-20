-- Own capability only; no moderator identity or report content is disclosed.
create function public.core_moderation_access() returns jsonb
language sql stable security definer set search_path='' as $$
  select jsonb_build_object('can_review',exists(
    select 1 from public.core_moderators m join auth.users u on u.id=m.user_id
    where m.user_id=auth.uid() and m.enabled and m.expires_at>now()
      and not coalesce(u.is_anonymous,false) and (u.banned_until is null or u.banned_until<now())
      and exists(select 1 from auth.sessions s where s.user_id=u.id and s.id=(auth.jwt()->>'session_id')::uuid)
  ));
$$;
revoke all on function public.core_moderation_access() from public,anon;
grant execute on function public.core_moderation_access() to authenticated;
