-- Preserve consent/version checks; location is part of the versioned agreement.
do $migration$
declare d text; old text;
begin
 d:=pg_get_functiondef('core_private.stealth_formation(text,jsonb)'::regprocedure);
 old:='''pickup_member'',''cost_change_note'']';
 if position(old in d)=0 then raise exception 'Unexpected formation whitelist'; end if;
 d:=replace(d,old,'''pickup_member'',''cost_change_note'',''location'']');
 old:='cost:=(v->>''cost_per_person_twd'')::integer;';
 if position(old in d)=0 then raise exception 'Unexpected cost assignment'; end if;
 d:=replace(d,old,$patch$
    if v ? 'location' and v->'location'<>'null'::jsonb then
      -- Reuse the same authoritative location validation as normal posts.
      perform core_private.post_input(jsonb_build_object('content',v->>'content',
        'tags',jsonb_build_array('偷揪成團'),'capacity',n,'deadline_at',v->'deadline_at',
        'price_per_person',v->'cost_per_person_twd','location',v->'location'));
    end if;
    cost:=(v->>'cost_per_person_twd')::integer;
 $patch$);
 old:='''location'',jsonb_build_object(''name'',place,''latitude'',24.9875,''longitude'',121.576,''address'',''校園地圖參考點；實際集合位置請依集合點名稱在聊天室確認。'',''source'',null)';
 if position(old in d)=0 then raise exception 'Unexpected formed location'; end if;
 d:=replace(d,old,'''location'',coalesce(nullif(p.terms->''location'',''null''::jsonb),jsonb_build_object(''name'',place,''latitude'',24.9875,''longitude'',121.576,''address'',''校園地圖參考點；實際集合位置請依集合點名稱在聊天室確認。'',''source'',null))');
 execute d;
end $migration$;

-- Authenticated participants only. Return shared confirmed tags, never private transcripts.
create or replace function core_private.stealth_view(p public.core_stealth_proposals,actor uuid) returns jsonb
language plpgsql stable set search_path='' as $$
declare v jsonb; shared jsonb;
begin
 v:=core_private.stealth_view_before_quick(p,actor);
 if v is null then return null; end if;
 select coalesce(jsonb_agg(item order by item),'[]'::jsonb) into shared from (
   select t.item from public.core_stealth_signals s
   cross join lateral jsonb_array_elements_text(coalesce(s.payload->'tags','[]'::jsonb)) t(item)
   where s.id=any(p.signals) and s.version=p.signal_versions[array_position(p.signals,s.id)]
     and char_length(t.item) between 2 and 40
     and t.item not in ('飲料','餐點','正餐','點心','零食','拼單','食物')
   group by t.item having count(distinct s.id)=cardinality(p.signals)
   order by t.item limit 6
 ) topics;
 return v||jsonb_build_object('shared_items',shared,'planning_members',case when v->>'status' in ('pending','ready') then
 (select coalesce(jsonb_agg(jsonb_build_object('number',x.n,'roles',s.payload->'roles','tags',s.payload->'tags',
 'location_id',s.payload->'location_id','starts_at',s.payload->'starts_at','ends_at',s.payload->'ends_at',
 'budget_max_twd',s.payload->'budget_max_twd','known_cost_twd',s.payload->'known_cost_twd') order by x.n),'[]'::jsonb)
 from unnest(p.signals) with ordinality x(id,n) join public.core_stealth_signals s on s.id=x.id)
 else '[]'::jsonb end,'coordination_opened',exists(select 1 from public.core_stealth_rooms where proposal_id=p.id));
end $$;

-- Opening a chat must not manufacture prices, volunteers or formal consent.
create or replace function core_private.stealth_ensure_initial_terms(p_id uuid)
returns public.core_stealth_proposals language plpgsql security definer set search_path='' as $$
declare p public.core_stealth_proposals;
begin
 select * into p from public.core_stealth_proposals where id=p_id for update;
 if not found then return null; end if;
 insert into public.core_stealth_rooms(proposal_id) values (p.id) on conflict do nothing;
 return p;
end $$;
revoke all on function core_private.stealth_ensure_initial_terms(uuid) from public,anon,authenticated;
