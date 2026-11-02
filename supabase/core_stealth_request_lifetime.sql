-- Keep meeting availability independent from the 24h request lifetime.
-- Modify only the verified validator/create clauses; retain RPC auth, RLS and locks.
do $migration$
declare definition text; old_clause text; new_clause text;
begin
  definition := pg_get_functiondef('core_private.stealth_validate(text,jsonb)'::regprocedure);
  if strpos(definition,'or ex>en')=0 then raise exception 'Unexpected signal validator'; end if;
  definition := replace(definition,'or ex>en', 'or ex>clock_timestamp()+interval ''24 hours''');
  old_clause := $old$'dietary_verified','roles'];$old$;
  new_clause := $new$'dietary_verified','roles','participation_preference'];$new$;
  if strpos(definition,old_clause)=0 then raise exception 'Unexpected allowed fields'; end if;
  definition := replace(definition,old_clause,new_clause);
  definition := replace(definition,
    $old$not v ?& allowed or v->>'kind'$old$,
    $new$not v ?& array_remove(allowed,'participation_preference') or v->>'kind'$new$);
  definition := replace(definition,
    $old$st:=(v->>'starts_at')::timestamptz;$old$,
    $new$if v ? 'participation_preference' and v->'participation_preference'<>'null'::jsonb and
      (jsonb_typeof(v->'participation_preference')<>'string' or v->>'participation_preference' not in ('receive','flexible')) then
      raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
    st:=(v->>'starts_at')::timestamptz;$new$);
  execute definition;

  definition := pg_get_functiondef('core_private.stealth_cloud(text,jsonb)'::regprocedure);
  old_clause := $old$if s.payload<>args->'value' then$old$;
  if strpos(definition,old_clause)=0 then raise exception 'Unexpected replay clause'; end if;
  definition := replace(definition,old_clause,
    $new$if jsonb_strip_nulls(s.payload-'expires_at')<>jsonb_strip_nulls((args->'value')-'expires_at') then$new$);
  old_clause := $old$insert into public.core_stealth_signals(owner_id,payload,request_key) values(actor,args->'value',(args->>'request_key')::uuid) returning * into s;$old$;
  if strpos(definition,old_clause)=0 then raise exception 'Unexpected create clause'; end if;
  definition := replace(definition,old_clause,
    $new$insert into public.core_stealth_signals(owner_id,payload,request_key,created_at)
      values(actor,jsonb_set(args->'value','{expires_at}',to_jsonb(ts+interval '24 hours')),(args->>'request_key')::uuid,ts) returning * into s;$new$);
  execute definition;
end;
$migration$;

-- Extend only still-live, unclosed requests. Do not resurrect expired/consumed requests.
update public.core_stealth_signals
set payload=jsonb_set(payload,'{expires_at}',to_jsonb(created_at+interval '24 hours'))
where status='active' and created_at>clock_timestamp()-interval '24 hours'
  and (payload->>'expires_at')::timestamptz>clock_timestamp();
