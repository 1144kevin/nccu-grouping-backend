-- Count account-owned unread notification events without exposing their contents.
create function public.core_notification_badge() returns jsonb
language sql stable security definer set search_path='' set statement_timeout='8s' as $$
 with actor as (select core_private.account_guard() as id), counts as (
  select count(*) filter (where n.kind='message')::integer as messages,
         count(*) filter (where n.kind<>'message')::integer as orders
  from public.core_notices n, actor where n.owner_id=actor.id and n.read_at is null
 ), penalties as (
  select count(*)::integer as total from core_private.moderation_penalties p, actor
  where p.target_id=actor.id and p.read_at is null
 ) select jsonb_build_object('messages',counts.messages,'orders',counts.orders,
   'penalties',penalties.total,'total',counts.messages+counts.orders+penalties.total)
 from counts,penalties;
$$;
revoke all on function public.core_notification_badge() from public,anon;
grant execute on function public.core_notification_badge() to authenticated;

-- Viewing the notification center reads order notices; the pending application itself stays pending.
do $migration$
declare definition text; original text:='kind in (''accepted'',''completed'')';
begin
 definition:=pg_get_functiondef('public.core_notifications(text,jsonb)'::regprocedure);
 if position(original in definition)=0 then raise exception 'Unexpected order notice read function'; end if;
 definition:=replace(definition,original,'kind in (''accepted'',''completed'',''join_request'')');
 execute definition;
end $migration$;
