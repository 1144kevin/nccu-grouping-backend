-- Preserve the existing validator, permissions and all non-location checks.
-- The three campus gates remain suggested aliases; free meeting places are allowed.
do $migration$
declare definition text;
begin
  definition := pg_get_functiondef('core_private.stealth_validate(text,jsonb)'::regprocedure);
  if strpos(definition, $old$or v->>'location_id' not in ('east_gate','main_gate','south_gate')$old$) = 0 then
    raise exception 'Unexpected stealth validator; inspect before applying';
  end if;
  definition := replace(definition,
    $old$or v->>'location_id' not in ('east_gate','main_gate','south_gate')$old$,
    $new$or char_length(btrim(v->>'location_id')) not between 1 and 120 or v->>'location_id' ~ '[[:cntrl:]]'$new$);
  execute definition;
end;
$migration$;
