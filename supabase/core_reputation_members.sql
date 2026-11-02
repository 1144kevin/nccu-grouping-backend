-- Include each member's real level for members who cannot read host-only applications.
do $migration$
declare definition text;
begin
 definition := pg_get_functiondef('core_private.social(text,jsonb)'::regprocedure);
 if position('''badge'',pr.badge,''host''' in definition)=0 then raise exception 'member shape changed'; end if;
 definition := replace(definition,'''badge'',pr.badge,''host''','''badge'',pr.badge,''level'',core_private.reputation_level(pr.id),''host''');
 execute definition;
end $migration$;
