-- Serialize Storage RLS with account lifecycle transactions, including ordinary uploads.
-- Recheck identity after waiting so an in-flight upload cannot outlive final deletion.
create or replace function core_private.media_access(operation text,object_name text) returns boolean
language plpgsql security definer set search_path='' as $$
declare actor uuid:=auth.uid(); asset public.core_media_assets;
begin
 if actor is null then return false; end if;
 perform pg_advisory_xact_lock(hashtextextended('core-account-safety-v1',0));
 perform 1 from auth.users where id=actor for key share;
 if not found or core_private.media_actor() is null then return false; end if;
 if core_private.account_pending(actor) then
  if operation not in ('delete','select') then return false; end if;
  select * into asset from public.core_media_assets a where a.owner_id=actor
   and object_name=a.owner_id::text||'/'||a.id::text||'.jpg' for update;
  return found;
 end if;
 return core_private.media_access_before_account_deletion(operation,object_name);
end $$;
