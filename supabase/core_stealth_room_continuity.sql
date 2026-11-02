-- Invitation expiry stops new matches, not an already-open, unanimously accepted chat.
-- Explicit withdrawal, blocks, revoked signals and cancelled formed orders still stop sends.
create function core_private.stealth_room_can_continue(p public.core_stealth_proposals) returns boolean
language sql stable security definer set search_path='' as $$
 select exists(select 1 from public.core_stealth_rooms where proposal_id=p.id)
  and not exists(select 1 from unnest(p.owners) u where coalesce(p.decisions->>u::text,'pending')<>'accepted')
  and (select count(*) from public.core_stealth_signals where id=any(p.signals) and status<>'revoked')=cardinality(p.signals)
  and (p.formed_post_id is null or exists(select 1 from public.core_posts where id=p.formed_post_id and not cancelled));
$$;
revoke all on function core_private.stealth_room_can_continue(public.core_stealth_proposals) from public,anon,authenticated;

do $migration$
declare definition text; marker text:='live:=v->>''status'' in (''ready'',''formed'');';
begin
 definition:=pg_get_functiondef('core_private.stealth_quick_before_inbox(text,jsonb)'::regprocedure);
 if position(marker in definition)=0 then raise exception 'Unexpected coordination-room implementation'; end if;
 definition:=replace(definition,marker,
  'live:=(v->>''status'' in (''ready'',''formed'') or core_private.stealth_room_can_continue(p))
   and not exists(select 1 from unnest(p.owners) u where coalesce(p.decisions->>u::text,''pending'')<>''accepted'')
   and not exists(select 1 from public.core_stealth_signals where id=any(p.signals) and status=''revoked'')
   and (p.formed_post_id is null or exists(select 1 from public.core_posts where id=p.formed_post_id and not cancelled));');
 execute definition;
end $migration$;
