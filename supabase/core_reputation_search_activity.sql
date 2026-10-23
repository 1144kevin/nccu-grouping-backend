-- Enrich already-authorized rows without replacing existing visibility filters.
do $migration$
declare definition text; marker text := 'return jsonb_build_object(''items'',result,''next_cursor'',next_cursor);';
begin
 definition := pg_get_functiondef('core_private.search(jsonb)'::regprocedure);
 if position(marker in definition)=0 then raise exception 'search function layout changed'; end if;
 definition := replace(definition, marker, $patch$
 select coalesce(jsonb_agg(item || coalesce((select jsonb_build_object('level',core_private.reputation_level(p.id),'badge',p.badge)
 from public.core_profiles p where p.id=case when item->>'kind'='user' then (item->>'id')::uuid
 when item->>'kind'='message' then (select sender_id from public.core_chat_messages where id=(item->>'id')::uuid) end),'{}'::jsonb) order by ordinal),'[]'::jsonb)
 into result from jsonb_array_elements(result) with ordinality entries(item,ordinal);
 return jsonb_build_object('items',result,'next_cursor',next_cursor);
 $patch$);
 execute definition;
 definition := pg_get_functiondef('core_private.order_activity(jsonb)'::regprocedure);
 if position(marker in definition)=0 then raise exception 'activity function layout changed'; end if;
 definition := replace(definition, marker, $patch$
 select coalesce(jsonb_agg(item || jsonb_build_object('requester_level',core_private.reputation_level((item->>'requester_id')::uuid),
 'requester_badge',(select badge from public.core_profiles where id=(item->>'requester_id')::uuid)) order by ordinal),'[]'::jsonb)
 into result from jsonb_array_elements(result) with ordinality entries(item,ordinal);
 return jsonb_build_object('items',result,'next_cursor',next_cursor);
 $patch$);
 execute definition;
end $migration$;
