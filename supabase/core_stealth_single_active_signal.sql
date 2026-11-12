-- Restrict each user to at most 1 active stealth signal at a time (24h lifetime).
-- Users must revoke/cancel their current active stealth signal before creating a new one.
do $migration$
declare definition text;
begin
  definition := pg_get_functiondef('core_private.stealth_cloud(text,jsonb)'::regprocedure);
  if strpos(definition, '>=10 then') > 0 then
    definition := replace(definition, '>=10 then', '>=1 then');
    execute definition;
  end if;
end;
$migration$;
