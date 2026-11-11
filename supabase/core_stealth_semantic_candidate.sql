-- Semantic proposals retain privacy, consent, account and lifecycle protections.
-- Differences in user preferences are explicitly left for participants to discuss.
create function core_private.stealth_semantic_candidate(ids uuid[], ts timestamptz) returns jsonb
language plpgsql security definer set search_path='' as $$
declare n integer:=cardinality(ids); owners uuid[]; ordered_ids uuid[]; versions integer[];
 policies integer[]; fingerprint text; anchor public.core_stealth_signals; st timestamptz;
 en timestamptz; ex timestamptz; unknowns jsonb:='[]'::jsonb;
begin
 if n<>2 or (select count(*) from public.core_stealth_signals where id=any(ids))<>n
   or (select count(distinct owner_id) from public.core_stealth_signals where id=any(ids))<>n then return null; end if;
 if exists(select 1 from public.core_stealth_signals s join auth.users u on u.id=s.owner_id
    where s.id=any(ids) and u.raw_app_meta_data->>'demo_batch'='showcase_20261017_v1')
   and exists(select 1 from public.core_stealth_signals s join public.core_stealth_agents a on a.owner_id=s.owner_id
    where s.id=any(ids) and not coalesce((a.payload->>'include_demo')::boolean,false)) then return null; end if;
 if (select count(distinct coalesce(u.raw_app_meta_data->>'stealth_test_scope','')) from public.core_stealth_signals s
   join auth.users u on u.id=s.owner_id where s.id=any(ids)
   and u.raw_app_meta_data->>'demo_batch' is distinct from 'showcase_20261017_v1')>1 then return null; end if;
 if (select count(*) from public.core_stealth_signals s join public.core_stealth_agents a on a.owner_id=s.owner_id
   where s.id=any(ids) and s.status='active' and (s.payload->>'expires_at')::timestamptz>ts
   and core_private.stealth_awake(a.payload,ts))<>n then return null; end if;
 select array_agg(s.owner_id order by s.owner_id),array_agg(s.id order by s.owner_id),
   array_agg(s.version order by s.owner_id),array_agg(a.policy_version order by s.owner_id),
   encode(sha256(convert_to(string_agg(s.id::text||':'||s.version||':'||a.policy_version,'|' order by s.id),'UTF8')),'hex')
 into owners,ordered_ids,versions,policies,fingerprint from public.core_stealth_signals s
 join public.core_stealth_agents a on a.owner_id=s.owner_id where s.id=any(ids);
 if exists(select 1 from unnest(owners) a cross join unnest(owners) b where core_private.users_blocked(a,b)) then return null; end if;
 select * into anchor from public.core_stealth_signals where id=ids[1];
 st:=greatest(ts,(anchor.payload->>'starts_at')::timestamptz);
 en:=(anchor.payload->>'ends_at')::timestamptz;
 if st>=en then return null; end if;
 select least(min((payload->>'expires_at')::timestamptz),ts+interval '30 minutes',en) into ex
 from public.core_stealth_signals where id=any(ids);
 if ex<=ts then return null; end if;
 unknowns:=jsonb_build_array('各自的時間、地點、品項與預算可能不同，請在聊天室協調確認',
 '下單、取餐與實際費用尚未確認', '飲食限制請向商家確認');
 return jsonb_build_object('owners',owners,'signals',ordered_ids,'signal_versions',versions,
 'policy_versions',policies,'fingerprint',fingerprint,'payload',jsonb_build_object(
 'category',anchor.payload->>'category','location_id',anchor.payload->>'location_id',
 'starts_at',st,'ends_at',en,'expires_at',ex,'budget_max_twd',null,'known_cost_twd',null,
 'member_count',n,'score',0,'reasons',jsonb_build_array('需求內容的語義相近，細節仍需雙方確認'),
 'unknowns',unknowns,'semantic_proposal',true,
 'is_demo',exists(select 1 from auth.users u where u.id=any(owners) and u.raw_app_meta_data->>'demo_batch'='showcase_20261017_v1')));
end $$;
revoke all on function core_private.stealth_semantic_candidate(uuid[],timestamptz) from public,anon,authenticated;

do $migration$
declare d text; old text;
begin
 d:=pg_get_functiondef('public.core_semantic_discovery(text,jsonb)'::regprocedure);
 old:='and x.payload->>''location_id''=s.payload->>''location_id''';
 if position(old in d)=0 then raise exception 'unexpected semantic location filter'; end if;
 d:=replace(d,old,'');
 old:='and core_private.stealth_candidate(array[s.id,x.id],ts) is not null';
 if position(old in d)=0 then raise exception 'unexpected semantic candidate filter'; end if;
 d:=replace(d,old,'');
 old:='candidate:=core_private.stealth_candidate(array[s.id,nearest.id],clock_timestamp());';
 if position(old in d)=0 then raise exception 'unexpected semantic candidate construction'; end if;
 d:=replace(d,old,'candidate:=core_private.stealth_semantic_candidate(array[s.id,nearest.id],clock_timestamp());');
 old:='candidate:=jsonb_set(candidate,''{payload,reasons}''';
 if position(old in d)=0 then raise exception 'unexpected semantic reasons'; end if;
 d:=replace(d,old,'candidate:=jsonb_set(candidate,''{payload,score}'',to_jsonb(greatest(0,least(100,round(nearest.similarity*100)::integer)))); candidate:=jsonb_set(candidate,''{payload,reasons}''');
 execute d;
 d:=pg_get_functiondef('core_private.stealth_view_before_quick(public.core_stealth_proposals,uuid)'::regprocedure);
 old:='''你的需求類型、取餐地點與時間符合這個方案''';
 if position(old in d)=0 then raise exception 'unexpected proposal explanation'; end if;
 d:=replace(d,old,'case when coalesce((p.payload->>''semantic_proposal'')::boolean,false) then ''需求內容相近；地點、時間和預算須與對方確認'' else ''你的需求類型、取餐地點與時間符合這個方案'' end');
 d:=replace(d,'''有一組時間與需求合得來的夥伴，想一起看看嗎？''','''有一組需求相近的夥伴，想一起看看嗎？''');
 execute d;
end $migration$;
