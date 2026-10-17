create or replace function core_private.stealth_view(p public.core_stealth_proposals,actor uuid) returns jsonb
language plpgsql stable set search_path='' as $$
declare valid boolean:=true; i integer; own_signal jsonb; own_agent jsonb; decision text; state text; accepted integer;
begin
  if actor is null or not actor=any(p.owners) then return null; end if;
  if (p.payload->>'expires_at')::timestamptz<=clock_timestamp() then valid:=false; end if;
  for i in 1..cardinality(p.owners) loop
    if not exists(select 1 from public.core_stealth_signals s join public.core_stealth_agents a on a.owner_id=s.owner_id
      where s.id=p.signals[i] and s.owner_id=p.owners[i] and s.status='active' and s.version=p.signal_versions[i]
      and (s.payload->>'expires_at')::timestamptz>clock_timestamp() and (a.payload->>'enabled')::boolean and a.policy_version=p.policy_versions[i]) then valid:=false; end if;
  end loop;
  decision:=coalesce(p.decisions->>actor::text,'pending');
  state:=case when p.formed_post_id is not null then 'formed'
    when not valid or exists(select 1 from jsonb_each_text(p.decisions) where value='declined') then 'unavailable'
    when (select count(*) from jsonb_each_text(p.decisions) where value='accepted')=cardinality(p.owners) then 'ready' else 'pending' end;
  select count(*) into accepted from unnest(p.owners) u where p.terms_revision>0 and p.terms_acceptances->>u::text=p.terms_revision::text;
  select payload into own_signal from public.core_stealth_signals where id=p.signals[array_position(p.owners,actor)];
  select payload into own_agent from public.core_stealth_agents where owner_id=actor;
  return p.payload||jsonb_build_object('id',p.id,'created_at',p.created_at,'my_decision',decision,'status',state,'my_signal_title',own_signal->>'title','my_roles',own_signal->'roles',
    'invitation_text',case own_agent->>'tone' when 'playful' then '分身幫你找到一組可以一起揪的人！' when 'direct' then '找到符合條件的互補配對。' else '有一組時間與需求合得來的夥伴，想一起看看嗎？' end,
    'why_me',jsonb_build_array('你的需求類型、取餐地點與時間符合這個方案','只有全員同意同一版費用與分工，才會建立拼單'),'is_llm',false,
    'terms',p.terms,'terms_revision',p.terms_revision,'my_member_number',array_position(p.owners,actor),
    'host_member_number',array_position(p.owners,p.terms_author_id),'confirmed_count',accepted,
    'my_terms_accepted',p.terms_revision>0 and coalesce(p.terms_acceptances->>actor::text,'')=p.terms_revision::text,
    'formed_post_id',p.formed_post_id,'formed_at',p.formed_at,
    'planning_members',case when state in ('pending','ready') then (select coalesce(jsonb_agg(jsonb_build_object('number',x.n,'roles',s.payload->'roles') order by x.n),'[]'::jsonb) from unnest(p.signals) with ordinality x(id,n) join public.core_stealth_signals s on s.id=x.id) else '[]'::jsonb end);
end; $$;
