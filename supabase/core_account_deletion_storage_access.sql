-- Storage DELETE first selects the objects. During confirmed cleanup, permit only
-- the deleting account's own exact reserved paths for SELECT/DELETE, never INSERT.
create or replace function core_private.media_access(operation text,object_name text) returns boolean
language plpgsql security definer set search_path='' as $$
declare actor uuid:=core_private.media_actor(); asset public.core_media_assets;
begin
 if actor is null then return false; end if;
 if core_private.account_pending(actor) then
  if operation not in ('delete','select') then return false; end if;
  perform pg_advisory_xact_lock(hashtextextended('core-account-safety-v1',0));
  select * into asset from public.core_media_assets a where a.owner_id=actor
   and object_name=a.owner_id::text||'/'||a.id::text||'.jpg' for update;
  return found;
 end if;
 return core_private.media_access_before_account_deletion(operation,object_name);
end $$;
