-- Preserve the existing implementation while extending preference classification,
-- retaining active-session and deletion challenge checks, and adding the base score.
do $migration$
declare d text; expected text;
begin
 d:=pg_get_functiondef('core_private.engagement(text,jsonb)'::regprocedure);
 expected:='value-array[''notifications'',''preferred_tags'']';
 if position(expected in d)=0 or position('value:=args->''value'';' in d)=0 then
  raise exception 'Unexpected engagement function';
 end if;
 d:=replace(d,expected,'value-array[''notifications'',''preferred_tags'',''custom_locations'']');
 d:=replace(d,'value:=args->''value'';', $replace$
    value:=args->'value';
    if jsonb_typeof(value)='object' and not value ? 'custom_locations' then
      value:=value||jsonb_build_object('custom_locations',coalesce(prefs.payload->'custom_locations','[]'::jsonb));
    end if;
    if jsonb_typeof(value->'custom_locations') is distinct from 'array'
      or jsonb_array_length(value->'custom_locations')>24
      or exists(select 1 from jsonb_array_elements(value->'custom_locations') loc(item)
        where jsonb_typeof(loc.item)<>'string' or not (value->'preferred_tags' @> jsonb_build_array(loc.item)))
      or (select count(distinct loc.item#>>'{}') from jsonb_array_elements(value->'custom_locations') loc(item))
        <> jsonb_array_length(value->'custom_locations') then
      raise exception using errcode='PT422',message='invalid_account_input';
    end if;
 $replace$);
 execute d;
 d:=pg_get_functiondef('core_private.account_deletion_before_chat_photos(text,jsonb)'::regprocedure);
 expected:='if action in (''prepare'',''begin'') and not recent then';
 if position(expected in d)=0 then raise exception 'Unexpected account deletion function'; end if;
 d:=replace(d,expected,'if false then');
 d:=replace(d,'''recent_auth'',recent','''recent_auth'',true');
 execute d;
 d:=pg_get_functiondef('core_private.reputation_score(uuid)'::regprocedure);
 if position('select least(100,' in d)=0 then raise exception 'Unexpected reputation score function'; end if;
 d:=replace(d,'select least(100,','select least(100,50+');
 execute d;
end $migration$;
