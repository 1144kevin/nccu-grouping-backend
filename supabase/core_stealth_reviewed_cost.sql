create or replace function core_private.stealth_formation(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=auth.uid(); p public.core_stealth_proposals; v jsonb; view jsonb; allowed text[];
  ts timestamptz:=clock_timestamp(); revision integer; deadline timestamptz; cost integer; n integer; i integer;
  post_payload jsonb; post_id uuid; order_name text; pickup_name text; place text;
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false)) or
    not exists(select 1 from auth.sessions where user_id=actor and id=(auth.jwt()->>'session_id')::uuid and (not_after is null or not_after>ts)) then
    raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  allowed:=case action when 'terms' then array['proposal_id','expected_revision','value'] when 'consent' then array['proposal_id','expected_revision','agree'] else null end;
  if allowed is null or jsonb_typeof(args) is distinct from 'object' or args-allowed<>'{}' or
    jsonb_typeof(args->'expected_revision') is distinct from 'number' or args->>'expected_revision' !~ '^[0-9]{1,2}$' then
    raise exception using errcode='PT422',message='invalid_formation_input'; end if;
  if not pg_try_advisory_xact_lock(hashtextextended('core_stealth_dev_v1',0)) then raise exception using errcode='PT409',message='stealth_busy_retry'; end if;
  select * into p from public.core_stealth_proposals q where q.id=(args->>'proposal_id')::uuid and actor=any(q.owners) for update;
  if not found then raise exception using errcode='PT404',message='stealth_not_found'; end if;
  revision:=(args->>'expected_revision')::integer;
  if revision<>p.terms_revision then raise exception using errcode='PT409',message='terms_revision_conflict'; end if;
  if action='consent' and args->'agree' is distinct from 'true'::jsonb then raise exception using errcode='PT422',message='explicit_consent_required'; end if;
  -- A response lost after the final commit can be retried without duplicate posts/members.
  if p.formed_post_id is not null then
    if action='consent' then return core_private.stealth_view(p,actor); end if;
    raise exception using errcode='PT409',message='proposal_already_formed'; end if;
  view:=core_private.stealth_view(p,actor);
  if view->>'status'='unavailable' then raise exception using errcode='PT409',message='proposal_unavailable'; end if;
  if coalesce((p.payload->>'is_demo')::boolean,false) then raise exception using errcode='PT409',message='demo_cannot_form'; end if;
  n:=cardinality(p.owners);
  if action='terms' then
    v:=args->'value';
    if jsonb_typeof(v) is distinct from 'object' or v-array['content','cost_per_person_twd','deadline_at','order_member','pickup_member','cost_change_note']<>'{}' or
      jsonb_typeof(v->'content') is distinct from 'string' or char_length(btrim(v->>'content')) not between 10 and 300 or
      jsonb_typeof(v->'cost_per_person_twd') is distinct from 'number' or v->>'cost_per_person_twd' !~ '^[0-9]{1,6}$' or
      jsonb_typeof(v->'deadline_at') is distinct from 'string' or v->>'deadline_at' !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' or
      jsonb_typeof(v->'order_member') is distinct from 'number' or v->>'order_member' !~ '^[1-3]$' or
      jsonb_typeof(v->'pickup_member') is distinct from 'number' or v->>'pickup_member' !~ '^[1-3]$' then
      raise exception using errcode='PT422',message='invalid_formation_input'; end if;
    cost:=(v->>'cost_per_person_twd')::integer; deadline:=(v->>'deadline_at')::timestamptz;
    if cost>100000 or (p.payload->>'budget_max_twd' is not null and cost>(p.payload->>'budget_max_twd')::integer) then raise exception using errcode='PT422',message='formation_cost_conflict'; end if;
    if v ? 'cost_change_note' and (jsonb_typeof(v->'cost_change_note') is distinct from 'string' or char_length(v->>'cost_change_note')>120) then raise exception using errcode='PT422',message='invalid_formation_input'; end if;
    if p.payload->>'known_cost_twd' is not null and cost<>(p.payload->>'known_cost_twd')::integer and coalesce(char_length(btrim(v->>'cost_change_note')),0) not between 5 and 120 then raise exception using errcode='PT422',message='formation_cost_note_required'; end if;
    if (v->>'order_member')::integer>n or (v->>'pickup_member')::integer>n or deadline<=ts or deadline>(p.payload->>'ends_at')::timestamptz then
      raise exception using errcode='PT422',message='invalid_formation_input'; end if;
    -- Exact replay preserves approvals. Any edit (including changing host) resets all approvals.
    if p.terms=v and p.terms_author_id=actor then return view; end if;
    if p.terms_revision>=20 then raise exception using errcode='PT409',message='terms_revision_limit'; end if;
    update public.core_stealth_proposals set terms=v,terms_revision=terms_revision+1,terms_author_id=actor,terms_acceptances='{}' where id=p.id returning * into p;
    return core_private.stealth_view(p,actor);
  end if;
  if p.terms is null or p.terms_revision=0 then raise exception using errcode='PT409',message='terms_required'; end if;
  deadline:=(p.terms->>'deadline_at')::timestamptz;
  if deadline<=ts then raise exception using errcode='PT409',message='formation_deadline_passed'; end if;
  update public.core_stealth_proposals set terms_acceptances=terms_acceptances||jsonb_build_object(actor::text,revision),
    decisions=decisions||jsonb_build_object(actor::text,'accepted') where id=p.id returning * into p;
  if exists(select 1 from unnest(p.owners) u where coalesce(p.terms_acceptances->>u::text,'')<>revision::text) then return core_private.stealth_view(p,actor); end if;
  -- Same transaction and feature lock as revoke/agent policy changes/other formations.
  perform 1 from public.core_stealth_signals where id=any(p.signals) order by id for update;
  if core_private.stealth_view(p,actor)->>'status'='unavailable' then raise exception using errcode='PT409',message='proposal_unavailable'; end if;
  select display_name into order_name from public.core_profiles where id=p.owners[(p.terms->>'order_member')::integer];
  select display_name into pickup_name from public.core_profiles where id=p.owners[(p.terms->>'pickup_member')::integer];
  place:=case p.payload->>'location_id' when 'main_gate' then '政大正門' when 'east_gate' then '政大東側門' else '政大南門' end;
  post_payload:=core_private.post_input(jsonb_build_object('content',btrim(p.terms->>'content')||E'\n下單：'||order_name||'；取餐：'||pickup_name||E'\n配合時段：'||(p.payload->>'starts_at')||' ～ '||(p.payload->>'ends_at'),
    'tags',jsonb_build_array('偷揪成團',p.payload->>'category'),'capacity',n,'deadline_at',deadline,'price_per_person',p.terms->'cost_per_person_twd',
    'location',jsonb_build_object('name',place,'latitude',24.9875,'longitude',121.576,'address','校園地圖參考點；實際集合位置請依集合點名稱在聊天室確認。','source',null)));
  insert into public.core_posts(author_id,payload,deadline_at,capacity,request_key,original_payload)
    values(p.terms_author_id,post_payload,deadline,n,'stealth-formation-'||p.id::text,post_payload) returning id into post_id;
  insert into public.core_join_requests(post_id,requester_id,status,decided_at)
    select post_id,u,'accepted',ts from unnest(p.owners) u where u<>p.terms_author_id;
  update public.core_stealth_signals set status='consumed',version=version+1 where id=any(p.signals);
  update public.core_stealth_proposals set formed_post_id=post_id,formed_at=ts where id=p.id returning * into p;
  -- Existing chat lists derive rooms from the post + accepted members; no fake user message needed.
  return core_private.stealth_view(p,actor);
exception when invalid_text_representation or numeric_value_out_of_range or invalid_datetime_format or datetime_field_overflow or character_not_in_repertoire then
  raise exception using errcode='PT422',message='invalid_formation_input';
end; $$;
