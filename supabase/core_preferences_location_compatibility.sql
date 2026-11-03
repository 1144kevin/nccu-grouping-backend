-- Older clients can remove a tag without leaving a stale location classification.
do $migration$
declare d text; marker text;
begin
 d:=pg_get_functiondef('core_private.engagement(text,jsonb)'::regprocedure);
 marker:='value:=value||jsonb_build_object(''custom_locations'',coalesce(prefs.payload->''custom_locations'',''[]''::jsonb));';
 if position(marker in d)=0 then raise exception 'Unexpected preference compatibility function'; end if;
 d:=replace(d,marker,$replace$
   value:=value||jsonb_build_object('custom_locations',
     (select coalesce(jsonb_agg(loc.item),'[]'::jsonb)
      from jsonb_array_elements(coalesce(prefs.payload->'custom_locations','[]'::jsonb)) loc(item)
      where coalesce(value->'preferred_tags','[]'::jsonb) @> jsonb_build_array(loc.item)));
 $replace$);
 execute d;
end $migration$;
