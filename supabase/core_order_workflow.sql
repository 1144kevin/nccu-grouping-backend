-- Persistent order stages, unanimous early formation, evidence and penalties.
create table core_private.order_workflow (
 post_id uuid primary key references public.core_posts(id) on delete cascade,
 stage text not null default 'waiting' check(stage in ('waiting','formed','en_route','delivering','completed')),
 formed_once boolean not null default false,
 revision integer not null default 0,
 updated_at timestamptz not null default clock_timestamp()
);
create table core_private.order_formation_votes (
 post_id uuid not null references public.core_posts(id) on delete cascade,
 member_id uuid not null references public.core_profiles(id) on delete cascade,
 revision integer not null,
 primary key(post_id,member_id)
);
create table core_private.order_evidence (
 id uuid primary key default gen_random_uuid(),
 post_id uuid not null references public.core_posts(id) on delete cascade,
 actor_id uuid not null references public.core_profiles(id) on delete cascade,
 image_id uuid not null unique references public.core_personal_images(id) on delete cascade,
 kind text not null check(kind in ('purchase','pickup')),
 created_at timestamptz not null default clock_timestamp(),
 unique(post_id,actor_id,kind)
);
create table core_private.order_penalties (
 id uuid primary key default gen_random_uuid(),
 post_id uuid not null references public.core_posts(id) on delete cascade,
 actor_id uuid not null references public.core_profiles(id) on delete cascade,
 points integer not null check(points in (1,3,5,10)),
 reason text not null,
 source_key text not null unique,
 created_at timestamptz not null default clock_timestamp()
);
create index order_penalties_actor on core_private.order_penalties(actor_id);
revoke all on core_private.order_workflow,core_private.order_formation_votes,core_private.order_evidence,core_private.order_penalties from public,anon,authenticated;

-- Existing completed orders remain completed. A full order is already formed;
-- all other orders start waiting, without inventing past departure/purchase events.
insert into core_private.order_workflow(post_id,stage,formed_once)
 select p.id,case when exists(select 1 from public.core_order_completions c where c.post_id=p.id) then 'completed'
 when 1+(select count(*) from public.core_join_requests j where j.post_id=p.id and j.status='accepted')>=p.capacity then 'formed'
 else 'waiting' end,
 exists(select 1 from public.core_order_completions c where c.post_id=p.id) or 1+(select count(*) from public.core_join_requests j where j.post_id=p.id and j.status='accepted')>=p.capacity from public.core_posts p;

create function core_private.order_workflow_notice(target uuid, state text, event_key text) returns void
language plpgsql security definer set search_path='' as $$
declare member uuid;
begin
 for member in select author_id from public.core_posts where id=target
   union select requester_id from public.core_join_requests where post_id=target and status='accepted'
 loop
  insert into public.core_notices(owner_id,kind,thread_kind,thread_id,source_key)
   values(member,'order_'||state,'order',target,event_key||':'||member::text) on conflict do nothing;
 end loop;
end $$;

-- The workflow row and post row are locked in the same transaction as every
-- membership change, so a departure cannot race a withdrawal penalty.
create function core_private.order_workflow_membership() returns trigger
language plpgsql security definer set search_path='' as $$
declare p public.core_posts; w core_private.order_workflow; joined integer; amount integer;
begin
 select * into p from public.core_posts where id=new.post_id for update;
 insert into core_private.order_workflow(post_id) values(p.id) on conflict do nothing;
 select * into w from core_private.order_workflow where post_id=p.id for update;
 if tg_op='INSERT' or old.status is distinct from new.status then
  if new.status='accepted' and w.stage<>'waiting' then
   raise exception using errcode='PT409',message='order_already_formed';
  end if;
  if tg_op='UPDATE' and old.status='accepted' and new.status='cancelled' and p.demo_batch is null and auth.uid()=new.requester_id and current_setting('app.order_explicit_action',true)='withdraw' then
   amount:=case w.stage when 'en_route' then 5 when 'delivering' then 10 else 1 end;
   insert into core_private.order_penalties(post_id,actor_id,points,reason,source_key)
    values(p.id,new.requester_id,amount,'member_withdraw','withdraw:'||new.id::text||':'||coalesce(new.decided_at,clock_timestamp())::text)
    on conflict(source_key) do nothing;
  end if;
  if tg_op='UPDATE' and old.status='accepted' and new.status<>'accepted' and w.stage='formed' then
   update core_private.order_workflow set stage='waiting',updated_at=clock_timestamp() where post_id=p.id returning * into w;
   perform core_private.order_workflow_notice(p.id,'waiting','reopened:'||p.id::text||':'||w.revision::text);
  end if;
  if w.stage='waiting' and (new.status='accepted' or (tg_op='UPDATE' and old.status='accepted')) then
   update core_private.order_workflow set revision=revision+1,updated_at=clock_timestamp() where post_id=p.id;
   delete from core_private.order_formation_votes where post_id=p.id;
   select 1+count(*) into joined from public.core_join_requests where post_id=p.id and status='accepted';
   if joined>=p.capacity then
    update core_private.order_workflow set stage='formed',formed_once=true,updated_at=clock_timestamp() where post_id=p.id;
    perform core_private.order_workflow_notice(p.id,'formed','formed:'||p.id::text);
   end if;
  end if;
 end if;
 return new;
end $$;
create trigger core_order_workflow_membership after insert or update of status on public.core_join_requests
 for each row execute function core_private.order_workflow_membership();

create function core_private.order_workflow_cancel() returns trigger
language plpgsql security definer set search_path='' as $$
declare w core_private.order_workflow; n integer; amount integer;
begin
 if not old.cancelled and new.cancelled and new.demo_batch is null and auth.uid()=new.author_id and current_setting('app.order_explicit_action',true)='cancel' then
  insert into core_private.order_workflow(post_id) values(new.id) on conflict do nothing;
  select * into w from core_private.order_workflow where post_id=new.id for update;
  select count(*) into n from public.core_join_requests where post_id=new.id and status='accepted';
  amount:=case when w.formed_once then 3 when n>0 then 1 else 0 end;
  if amount>0 then
   insert into core_private.order_penalties(post_id,actor_id,points,reason,source_key)
    values(new.id,new.author_id,amount,'host_cancel','cancel:'||new.id::text) on conflict(source_key) do nothing;
  end if;
  perform core_private.order_workflow_notice(new.id,'cancelled','cancel:'||new.id::text);
 end if;
 return new;
end $$;
create trigger core_order_workflow_cancel after update of cancelled on public.core_posts
 for each row execute function core_private.order_workflow_cancel();

create function public.core_order_workflow(action text, args jsonb default '{}'::jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=core_private.account_guard(); p public.core_posts; w core_private.order_workflow;
 member boolean; member_count integer; vote_count integer; image uuid; result jsonb;
begin
 if action not in ('view','vote','depart','purchase','complete') or jsonb_typeof(args) is distinct from 'object'
   or args-array['post_id','image_id'] <> '{}'::jsonb then
  raise exception using errcode='PT422',message='invalid_order_workflow'; end if;
 select * into p from public.core_posts where id=(args->>'post_id')::uuid for update;
 if not found then raise exception using errcode='PT404',message='post_not_found'; end if;
 member:=p.author_id=actor or exists(select 1 from public.core_join_requests where post_id=p.id and requester_id=actor and status='accepted');
 if not member then raise exception using errcode='PT403',message='chat_members_only'; end if;
 insert into core_private.order_workflow(post_id) values(p.id) on conflict do nothing;
 select * into w from core_private.order_workflow where post_id=p.id for update;
 if action<>'view' and (p.cancelled or w.stage='completed' or p.demo_batch is not null) then
  raise exception using errcode='PT409',message='order_unavailable'; end if;
 select 1+count(*) into member_count from public.core_join_requests where post_id=p.id and status='accepted';
 if action='vote' then
  if w.stage<>'waiting' or member_count<2 then raise exception using errcode='PT409',message='formation_not_ready'; end if;
  if actor<>p.author_id and not exists(select 1 from core_private.order_formation_votes where post_id=p.id and member_id=p.author_id and revision=w.revision) then
   raise exception using errcode='PT409',message='formation_host_first'; end if;
  insert into core_private.order_formation_votes(post_id,member_id,revision) values(p.id,actor,w.revision)
   on conflict(post_id,member_id) do update set revision=excluded.revision;
  if actor=p.author_id then
   perform core_private.order_workflow_notice(p.id,'formation_pending','formation:'||p.id::text||':'||w.revision::text);
  end if;
  select count(*) into vote_count from core_private.order_formation_votes v
   where v.post_id=p.id and v.revision=w.revision and (v.member_id=p.author_id or exists
    (select 1 from public.core_join_requests j where j.post_id=p.id and j.requester_id=v.member_id and j.status='accepted'));
  if vote_count=member_count then
   update core_private.order_workflow set stage='formed',formed_once=true,updated_at=clock_timestamp() where post_id=p.id returning * into w;
   perform core_private.order_workflow_notice(p.id,'formed','formed:'||p.id::text);
  end if;
 elsif action='depart' then
  if actor<>p.author_id then raise exception using errcode='PT403',message='host_required'; end if;
  if w.stage<>'formed' or member_count<2 then raise exception using errcode='PT409',message='order_not_formed'; end if;
  update core_private.order_workflow set stage='en_route',updated_at=clock_timestamp() where post_id=p.id returning * into w;
  perform core_private.order_workflow_notice(p.id,'en_route','departure:'||p.id::text);
 elsif action in ('purchase','complete') then
  if action='purchase' and (actor<>p.author_id or w.stage<>'en_route') then
   raise exception using errcode='PT409',message='purchase_not_ready'; end if;
  if action='complete' and w.stage<>'delivering' then raise exception using errcode='PT409',message='handoff_not_ready'; end if;
  if action='purchase' or actor<>p.author_id then
   image:=(args->>'image_id')::uuid;
   if not exists(select 1 from public.core_personal_images where id=image and owner_id=actor and purpose='evidence' and state='ready') then
    raise exception using errcode='PT422',message='order_photo_required'; end if;
   insert into core_private.order_evidence(post_id,actor_id,image_id,kind)
    values(p.id,actor,image,case when action='purchase' then 'purchase' else 'pickup' end)
    on conflict(post_id,actor_id,kind) do nothing;
  end if;
  if action='purchase' then
   update core_private.order_workflow set stage='delivering',updated_at=clock_timestamp() where post_id=p.id returning * into w;
   perform core_private.order_workflow_notice(p.id,'delivering','purchase:'||p.id::text);
  else
   insert into public.core_handoff_receipts(post_id,member_id) values(p.id,actor) on conflict do nothing;
   if exists(select 1 from public.core_order_completions where post_id=p.id) then
    update core_private.order_workflow set stage='completed',updated_at=clock_timestamp() where post_id=p.id returning * into w;
   end if;
  end if;
 end if;
 select count(*) into vote_count from core_private.order_formation_votes v where v.post_id=p.id and v.revision=w.revision;
 result:=jsonb_build_object('stage',w.stage,'member_count',member_count,'formation_votes',vote_count,
  'my_vote',exists(select 1 from core_private.order_formation_votes where post_id=p.id and member_id=actor and revision=w.revision),
  'host_vote',exists(select 1 from core_private.order_formation_votes where post_id=p.id and member_id=p.author_id and revision=w.revision),
  'is_host',actor=p.author_id,'completed_count',(select count(*) from public.core_handoff_receipts where post_id=p.id),
  'my_completed',exists(select 1 from public.core_handoff_receipts where post_id=p.id and member_id=actor));
 return result;
exception when invalid_text_representation then raise exception using errcode='PT422',message='invalid_order_workflow';
end $$;
revoke all on function public.core_order_workflow(text,jsonb) from public,anon;
grant execute on function public.core_order_workflow(text,jsonb) to authenticated;

-- The workflow RPC is the only route allowed to create a new completion receipt.
create function core_private.order_receipt_guard() returns trigger
language plpgsql security definer set search_path='' as $$
begin
 if current_setting('app.order_workflow_post',true) is distinct from new.post_id::text then
  raise exception using errcode='PT409',message='order_workflow_required';
 end if;
 return new;
end $$;
create trigger core_order_receipt_guard before insert on public.core_handoff_receipts
 for each row execute function core_private.order_receipt_guard();

do $migration$
declare d text; needle text;
begin
 d:=pg_get_functiondef('public.core_order_workflow(text,jsonb)'::regprocedure);
 needle:='insert into public.core_handoff_receipts(post_id,member_id) values(p.id,actor) on conflict do nothing;';
 if position(needle in d)=0 then raise exception 'Unexpected order workflow completion'; end if;
 d:=replace(d,needle,'perform set_config(''app.order_workflow_post'',p.id::text,true); '||needle);
 execute d;
 d:=pg_get_functiondef('core_private.reputation_score(uuid)'::regprocedure);
 needle:=' - coalesce((select sum(points) from core_private.moderation_penalties where target_id=target),0)::integer';
 if position(needle in d)=0 then raise exception 'Unexpected reputation score'; end if;
 d:=replace(d,needle,needle||' - coalesce((select sum(points) from core_private.order_penalties where actor_id=target),0)::integer');
 execute d;
end $migration$;

-- Only moderators can retrieve attached order evidence. Once attached, the owner
-- cannot delete it through the generic personal-image draft endpoint.
create or replace function core_private.personal_image_access(operation text,object_name text) returns boolean
language plpgsql security definer set search_path='' as $$
declare actor uuid:=core_private.media_actor(); asset public.core_personal_images; attached boolean; order_attached boolean; moderator boolean;
begin
 if actor is null then return false; end if;
 perform pg_advisory_xact_lock(hashtextextended('core-account-safety-v1',0));
 select * into asset from public.core_personal_images where object_name=id::text||'.jpg' for update;
 if not found then return false; end if;
 order_attached:=exists(select 1 from core_private.order_evidence where image_id=asset.id);
 attached:=order_attached or exists(select 1 from public.core_profiles where avatar_id=asset.id)
   or exists(select 1 from public.core_reports where payload->>'evidence_image_id'=asset.id::text);
 moderator:=exists(select 1 from public.core_moderators where user_id=actor and enabled and expires_at>clock_timestamp());
 if core_private.account_pending(actor) then return operation='delete' and asset.owner_id=actor; end if;
 return case operation
 when 'insert' then asset.owner_id=actor and asset.state='pending'
 when 'select' then (asset.owner_id=actor and not order_attached) or (asset.state='ready' and (
   (asset.purpose='avatar' and exists(select 1 from public.core_profiles where avatar_id=asset.id)
    and not core_private.users_blocked(actor,asset.owner_id) and not core_private.account_pending(asset.owner_id))
   or (asset.purpose='evidence' and attached and moderator)))
 when 'delete' then asset.owner_id=actor and not attached
 else false end;
end $$;

create function public.core_order_evidence_for_moderator(target uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare actor uuid:=core_private.account_guard(); result jsonb;
begin
 if not exists(select 1 from public.core_moderators where user_id=actor and enabled and expires_at>clock_timestamp()) then
  raise exception using errcode='PT403',message='moderator_required'; end if;
 select coalesce(jsonb_agg(jsonb_build_object('id',e.id,'image_id',e.image_id,'actor_id',e.actor_id,
  'kind',e.kind,'created_at',e.created_at) order by e.created_at),'[]'::jsonb) into result
 from core_private.order_evidence e where e.post_id=target;
 return jsonb_build_object('items',result);
end $$;
revoke all on function public.core_order_evidence_for_moderator(uuid) from public,anon;
grant execute on function public.core_order_evidence_for_moderator(uuid) to authenticated;

create function core_private.order_penalty_notice() returns trigger
language plpgsql security definer set search_path='' as $$
begin
 insert into public.core_notices(owner_id,kind,thread_kind,thread_id,source_key)
 values(new.actor_id,'order_penalty','order',new.post_id,'penalty:'||new.id::text) on conflict do nothing;
 return new;
end $$;
create trigger core_order_penalty_notice after insert on core_private.order_penalties
 for each row execute function core_private.order_penalty_notice();

create function public.core_order_workflow_notices(action text) returns jsonb
language plpgsql security definer set search_path='' as $$
declare actor uuid:=core_private.account_guard(); result jsonb;
begin
 if action='read' then
  update public.core_notices set read_at=coalesce(read_at,clock_timestamp()) where owner_id=actor and kind like 'order_%';
  return jsonb_build_object('ok',true);
 elsif action<>'list' then raise exception using errcode='PT422',message='invalid_notification_input'; end if;
 select coalesce(jsonb_agg(jsonb_build_object('id',n.id,'kind',n.kind,'post_id',n.thread_id,
  'created_at',n.created_at,'read_at',n.read_at,'points',p.points,
  'post_content',left(coalesce(post.payload->>'content',''),160)) order by n.created_at desc),'[]'::jsonb)
 into result from (select * from public.core_notices where owner_id=actor and (kind like 'order_%' or kind='completed')
  order by created_at desc,id desc limit 100) n
 left join core_private.order_penalties p on n.source_key='penalty:'||p.id::text
 left join public.core_posts post on post.id=n.thread_id;
 return jsonb_build_object('items',result);
end $$;
revoke all on function public.core_order_workflow_notices(text) from public,anon;
grant execute on function public.core_order_workflow_notices(text) to authenticated;

do $migration$
declare d text; old text:='''completed'',core_private.handoff_complete(post.id),';
begin
 d:=pg_get_functiondef('core_private.post_view(uuid,uuid)'::regprocedure);
 if position(old in d)=0 then raise exception 'Unexpected post view'; end if;
 d:=replace(d,old,old||'''workflow_stage'',coalesce((select stage from core_private.order_workflow where post_id=post.id),''waiting''),''workflow_formed_once'',coalesce((select formed_once from core_private.order_workflow where post_id=post.id),false),');
 execute d;
end $migration$;

do $migration$
declare d text; old text:='if post.deadline_at<=clock_timestamp() then raise exception using errcode=''PT409'',message=''post_deadline_passed''; end if;';
begin
 d:=pg_get_functiondef('core_private.dispatch(text,jsonb)'::regprocedure);
 if position(old in d)=0 then raise exception 'Unexpected post deadline guard'; end if;
 d:=replace(d,old,'if post.deadline_at<=clock_timestamp() and action not in (''withdraw'',''cancel'') then raise exception using errcode=''PT409'',message=''post_deadline_passed''; end if;');
 old:='update public.core_join_requests set status=''cancelled'',decided_at=clock_timestamp() where id=request.id;';
 if position(old in d)=0 then raise exception 'Unexpected withdrawal operation'; end if;
 d:=replace(d,old,'perform set_config(''app.order_explicit_action'',''withdraw'',true); '||old);
 old:='update public.core_posts set cancelled=true where id=post.id returning * into post;';
 if position(old in d)=0 then raise exception 'Unexpected cancellation operation'; end if;
 d:=replace(d,old,'perform set_config(''app.order_explicit_action'',''cancel'',true); '||old);
 execute d;
end $migration$;

revoke all on function core_private.order_workflow_notice(uuid,text,text) from public,anon,authenticated;
revoke all on function core_private.order_workflow_membership() from public,anon,authenticated;
revoke all on function core_private.order_workflow_cancel() from public,anon,authenticated;
revoke all on function core_private.order_receipt_guard() from public,anon,authenticated;
revoke all on function core_private.order_penalty_notice() from public,anon,authenticated;
