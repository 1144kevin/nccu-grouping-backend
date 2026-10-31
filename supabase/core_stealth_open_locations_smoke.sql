-- Transaction-safe synthetic validator checks; no tables or accounts are written.
do $test$
declare v jsonb; candidate text; rejected boolean; ts timestamptz := clock_timestamp();
begin
  v := jsonb_build_object('kind','need','category','drink','acceptable_categories','[]'::jsonb,
    'title','Synthetic open meeting place','location_id','台北101大門',
    'starts_at',ts,'ends_at',ts+interval '12 hours',
    'expires_at',ts+interval '12 hours','budget_max_twd',null,
    'known_cost_twd',null,'tags','[]'::jsonb,'dietary_requirements','[]'::jsonb,
    'dietary_verified','[]'::jsonb,'roles','[]'::jsonb);
  perform core_private.stealth_validate('signal',v);
  foreach candidate in array array['main_gate','east_gate','south_gate','台北車站南一門'] loop
    perform core_private.stealth_validate('signal',jsonb_set(v,'{location_id}',to_jsonb(candidate)));
  end loop;
  foreach candidate in array array['',repeat('x',121),E'bad\nplace'] loop
    rejected:=false;
    begin
      perform core_private.stealth_validate('signal',jsonb_set(v,'{location_id}',to_jsonb(candidate)));
    exception when sqlstate 'PT422' then rejected:=true;
    end;
    if not rejected then raise exception 'Invalid location accepted'; end if;
  end loop;
end;
$test$;
