-- The database already assigns the authoritative 24-hour expiration on insert.
-- Validate that same timestamp instead of a preview created on another clock.
do $migration$
declare definition text; expected text;
begin
  definition := pg_get_functiondef('core_private.stealth_cloud(text,jsonb)'::regprocedure);
  expected := 'perform core_private.stealth_validate(''signal'',args->''value'');';
  if position(expected in definition) = 0 then
    raise exception 'Unexpected stealth creation function; no change applied';
  end if;
  definition := replace(definition, expected,
    'perform core_private.stealth_validate(''signal'',jsonb_set(args->''value'',''{expires_at}'',to_jsonb(ts+interval ''24 hours'')));');
  execute definition;
end $migration$;
