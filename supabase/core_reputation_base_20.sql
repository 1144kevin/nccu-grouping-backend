-- Apply the same 20-point base to all accounts while preserving earned points and penalties.
do $migration$
declare definition text;
begin
  definition := pg_get_functiondef('core_private.reputation_score(uuid)'::regprocedure);
  if position('select greatest(0,least(100,50+' in definition) = 0 then
    raise exception 'Unexpected reputation score function; base was not changed';
  end if;
  definition := replace(definition, 'select greatest(0,least(100,50+',
                        'select greatest(0,least(100,20+');
  execute definition;
end $migration$;
