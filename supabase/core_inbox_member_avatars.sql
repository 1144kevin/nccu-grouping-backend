-- Only mutually accepted rooms expose up to two other participants for inbox avatars.
-- Preserve the existing inbox visibility, blocking, pagination and unread rules.
alter function core_private.social_thread(text,uuid,uuid) rename to social_thread_before_avatars;
revoke all on function core_private.social_thread_before_avatars(text,uuid,uuid) from public,anon,authenticated;

create function core_private.social_thread(k text,target uuid,actor uuid) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare result jsonb; proposal public.core_stealth_proposals; members jsonb; view jsonb;
begin
 result:=core_private.social_thread_before_avatars(k,target,actor);
 if result is null or k<>'stealth' then return result; end if;
 select * into proposal from public.core_stealth_proposals where id=target and actor=any(owners);
 view:=core_private.stealth_view(proposal,actor);
 if not found or exists(select 1 from unnest(proposal.owners) u where coalesce(proposal.decisions->>u::text,'pending')<>'accepted') or not (coalesce((view->>'coordination_opened')::boolean,false) or view->>'status' in ('ready','formed')) then
  return result||jsonb_build_object('avatar_members','[]'::jsonb);
 end if;
 select coalesce(jsonb_agg(jsonb_build_object('id',m.id,'name',m.display_name,'badge',m.badge) order by m.position),'[]'::jsonb)
 into members from (
  select p.id,p.display_name,p.badge,u.position from unnest(proposal.owners) with ordinality u(id,position)
  join public.core_profiles p on p.id=u.id
  where p.id<>actor and not core_private.account_pending(p.id)
   and not core_private.users_blocked(actor,p.id)
  order by u.position limit 2
 ) m;
 return result||jsonb_build_object('avatar_members',members);
end $$;
revoke all on function core_private.social_thread(text,uuid,uuid) from public,anon,authenticated;
