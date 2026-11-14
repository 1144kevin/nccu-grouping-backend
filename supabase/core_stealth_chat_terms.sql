-- Store proposal revisions as distinct, server-created chat events.
alter table public.core_stealth_room_messages add column event_kind text;
alter table public.core_stealth_room_messages add column terms_revision integer;
alter table public.core_stealth_room_messages add column event_terms jsonb;
alter table public.core_stealth_room_messages add constraint stealth_room_event_kind
 check (event_kind is null or event_kind in ('terms','formed'));

do $migration$
declare d text; old text; replacement text;
begin
 d:=pg_get_functiondef('core_private.stealth_formation(text,jsonb)'::regprocedure);
 old:='if cost>100000 or (p.payload->>''budget_max_twd'' is not null and cost>(p.payload->>''budget_max_twd'')::integer)';
 if position(old in d)=0 then raise exception 'Unexpected budget guard'; end if;
 d:=replace(d,old,'if cost>100000');
 old:='if p.payload->>''known_cost_twd'' is not null and cost<>(p.payload->>''known_cost_twd'')::integer and coalesce(char_length(btrim(v->>''cost_change_note'')),0) not between 5 and 120 then raise exception using errcode=''PT422'',message=''formation_cost_note_required''; end if;';
 if position(old in d)=0 then raise exception 'Unexpected cost-note guard'; end if;
 d:=replace(d,old,'');
 old:='or deadline>(p.payload->>''ends_at'')::timestamptz';
 if position(old in d)=0 then raise exception 'Unexpected deadline guard'; end if;
 d:=replace(d,old,'or deadline>ts+interval ''14 days''');
 old:='||E''\n配合時段：''||(p.payload->>''starts_at'')||'' ～ ''||(p.payload->>''ends_at'')';
 if position(old in d)=0 then raise exception 'Unexpected initial time in final post'; end if;
 d:=replace(d,old,'');
 old:='update public.core_stealth_proposals set terms=v,terms_revision=terms_revision+1,terms_author_id=actor,terms_acceptances=''{}'' where id=p.id returning * into p;';
 if position(old in d)=0 then raise exception 'Unexpected terms update'; end if;
 replacement:=old||E'\n'||'insert into public.core_stealth_room_messages(proposal_id,sender_id,content,request_key,event_kind,terms_revision,event_terms) values(p.id,actor,''提出了拼單方案'',gen_random_uuid(),''terms'',p.terms_revision,v);';
 d:=replace(d,old,replacement);
 old:='values(p.terms_author_id,post_payload,deadline,n,''stealth-formation-''||p.id::text,post_payload)';
 if position(old in d)=0 then raise exception 'Unexpected buyer ownership'; end if;
 d:=replace(d,old,'values(p.owners[(p.terms->>''order_member'')::integer],post_payload,deadline,n,''stealth-formation-''||p.id::text,post_payload)');
 old:='select post_id,u,''accepted'',ts from unnest(p.owners) u where u<>p.terms_author_id;';
 if position(old in d)=0 then raise exception 'Unexpected membership assignment'; end if;
 d:=replace(d,old,'select post_id,u,''accepted'',ts from unnest(p.owners) u where u<>p.owners[(p.terms->>''order_member'')::integer];');
 old:='update public.core_stealth_proposals set formed_post_id=post_id,formed_at=ts where id=p.id returning * into p;';
 if position(old in d)=0 then raise exception 'Unexpected formation update'; end if;
 d:=replace(d,old,old||E'\n'||'insert into public.core_stealth_room_messages(proposal_id,sender_id,content,request_key,event_kind,terms_revision) values(p.id,actor,''偷揪已成立'',gen_random_uuid(),''formed'',p.terms_revision);');
 execute d;

 d:=pg_get_functiondef('core_private.stealth_quick(text,jsonb)'::regprocedure);
 old:='m||jsonb_build_object(''image_id'',(select asset_id from public.core_chat_photos where message_id=(m->>''id'')::uuid))';
 if position(old in d)=0 then raise exception 'Unexpected room serializer'; end if;
 d:=replace(d,old,'m||jsonb_build_object(''image_id'',(select asset_id from public.core_chat_photos where message_id=(m->>''id'')::uuid),''event_kind'',(select event_kind from public.core_stealth_room_messages where id=(m->>''id'')::uuid),''terms_revision'',(select terms_revision from public.core_stealth_room_messages where id=(m->>''id'')::uuid),''event_terms'',(select event_terms from public.core_stealth_room_messages where id=(m->>''id'')::uuid))');
 execute d;
end $migration$;

create function public.core_stealth_formed_roles(post_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare actor uuid:=core_private.account_guard(); p public.core_stealth_proposals;
begin
 select * into p from public.core_stealth_proposals q where q.formed_post_id=post_id and actor=any(q.owners);
 if not found then raise exception using errcode='PT404',message='stealth_not_found'; end if;
 return jsonb_build_object('buyer_id',p.owners[(p.terms->>'order_member')::integer],
                           'pickup_id',p.owners[(p.terms->>'pickup_member')::integer]);
end $$;
revoke all on function public.core_stealth_formed_roles(uuid) from public,anon;
grant execute on function public.core_stealth_formed_roles(uuid) to authenticated;

create function public.core_stealth_history(cursor_time timestamptz default null,cursor_id uuid default null,page_limit integer default 20)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare actor uuid:=core_private.account_guard(); items jsonb; has_more boolean;
begin
 if page_limit is null or page_limit not between 1 and 50 or (cursor_time is null)<>(cursor_id is null) then
  raise exception using errcode='PT422',message='invalid_stealth_input'; end if;
 with rows as (
  select s.*, (select p.formed_post_id from public.core_stealth_proposals p
    where s.id=any(p.signals) and actor=any(p.owners) and p.formed_post_id is not null
    order by p.formed_at desc limit 1) post_id
  from public.core_stealth_signals s where s.owner_id=actor
   and (cursor_time is null or (s.created_at,s.id)<(cursor_time,cursor_id))
  order by s.created_at desc,s.id desc limit page_limit+1
 ), numbered as (select *,row_number() over(order by created_at desc,id desc) n from rows)
 select coalesce(jsonb_agg(jsonb_build_object('id',id,'title',payload->>'title',
   'category',payload->>'category','created_at',created_at,'location_id',payload->>'location_id',
   'starts_at',payload->>'starts_at','ends_at',payload->>'ends_at','budget_max_twd',payload->'budget_max_twd','status',
   case when post_id is not null then 'formed' when status='active' and (payload->>'expires_at')::timestamptz<=clock_timestamp() then 'expired' else status end,
   'formed_post_id',post_id) order by created_at desc,id desc) filter(where n<=page_limit),'[]'::jsonb),
   count(*)>page_limit into items,has_more from numbered;
 return jsonb_build_object('items',items,'has_more',has_more);
end $$;
revoke all on function public.core_stealth_history(timestamptz,uuid,integer) from public,anon;
grant execute on function public.core_stealth_history(timestamptz,uuid,integer) to authenticated;

create or replace function core_private.stealth_ensure_initial_terms(p_id uuid)
returns public.core_stealth_proposals
language plpgsql
security definer
set search_path = ''
as $$
declare
  p public.core_stealth_proposals;
  cat_label text;
  place_label text;
  summary_text text;
  cost_val integer;
  deadline_val timestamptz;
  order_m integer;
  pickup_m integer;
  initial_terms jsonb;
  msg_ts timestamptz;
begin
  select * into p from public.core_stealth_proposals where id = p_id for update;
  if not found then
    return null;
  end if;

  insert into public.core_stealth_rooms(proposal_id)
  values (p.id)
  on conflict do nothing;

  if p.terms is not null and p.terms_revision > 0 then
    if not exists (
      select 1 from public.core_stealth_room_messages
      where proposal_id = p.id and event_kind = 'terms'
    ) then
      select coalesce(min(created_at) - interval '1 second', clock_timestamp())
        into msg_ts
        from public.core_stealth_room_messages
        where proposal_id = p.id;
      insert into public.core_stealth_room_messages(
        proposal_id, sender_id, content, request_key, event_kind, terms_revision, event_terms, created_at
      ) values (
        p.id, coalesce(p.terms_author_id, p.owners[1]), '提出了拼單方案', gen_random_uuid(), 'terms', p.terms_revision, p.terms, msg_ts
      );
    end if;
    return p;
  end if;

  cat_label := case p.payload->>'category'
    when 'pizza' then '披薩'
    when 'meal' then '正餐'
    when 'drink' then '飲料'
    when 'bakery' then '麵包'
    when 'snack' then '零食'
    else '餐點'
  end;

  place_label := case p.payload->>'location_id'
    when 'main_gate' then '政大正門'
    when 'east_gate' then '政大東側門'
    when 'south_gate' then '政大南門'
    else coalesce(nullif(btrim(p.payload->>'location_id'), ''), '政大正門')
  end;

  summary_text := '一起分享' || cat_label || '，在' || place_label || '協調集合。每人費用、下單與取餐依全員最後確認的方案處理。';

  select coalesce(
    (p.payload->>'known_cost_twd')::integer,
    (p.payload->>'budget_max_twd')::integer,
    (select min((s.payload->>'known_cost_twd')::integer) from public.core_stealth_signals s where s.id = any(p.signals)),
    (select min((s.payload->>'budget_max_twd')::integer) from public.core_stealth_signals s where s.id = any(p.signals)),
    case p.payload->>'category'
      when 'drink' then 80
      when 'snack' then 80
      when 'bakery' then 80
      else 120
    end
  ) into cost_val;

  deadline_val := greatest(
    coalesce((p.payload->>'ends_at')::timestamptz, clock_timestamp() + interval '24 hours'),
    clock_timestamp() + interval '30 minutes'
  );

  select coalesce(
    (
      select x.n::integer
      from unnest(p.signals) with ordinality x(id, n)
      join public.core_stealth_signals s on s.id = x.id
      where s.payload->'roles' ? 'order'
      order by x.n
      limit 1
    ),
    1
  ) into order_m;

  select coalesce(
    (
      select x.n::integer
      from unnest(p.signals) with ordinality x(id, n)
      join public.core_stealth_signals s on s.id = x.id
      where s.payload->'roles' ? 'pickup'
      order by x.n
      limit 1
    ),
    case when cardinality(p.owners) >= 2 then 2 else 1 end
  ) into pickup_m;

  initial_terms := jsonb_build_object(
    'content', summary_text,
    'cost_per_person_twd', cost_val,
    'deadline_at', to_char(deadline_val at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'order_member', order_m,
    'pickup_member', pickup_m
  );

  update public.core_stealth_proposals
  set terms = initial_terms,
      terms_revision = 1,
      terms_author_id = p.owners[1],
      terms_acceptances = '{}'::jsonb
  where id = p.id
  returning * into p;

  select coalesce(min(created_at) - interval '1 second', clock_timestamp())
    into msg_ts
    from public.core_stealth_room_messages
    where proposal_id = p.id;

  insert into public.core_stealth_room_messages(
    proposal_id, sender_id, content, request_key, event_kind, terms_revision, event_terms, created_at
  ) values (
    p.id, p.owners[1], '提出了拼單方案', gen_random_uuid(), 'terms', 1, initial_terms, msg_ts
  );

  return p;
end $$;

