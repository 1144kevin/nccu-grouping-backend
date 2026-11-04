-- Moderation penalties and in-app notices are created atomically with an upheld review.
create table core_private.moderation_penalties (
 id uuid primary key default gen_random_uuid(),
 report_id uuid not null unique,
 target_id uuid not null references public.core_profiles(id) on delete cascade,
 moderator_id uuid references public.core_profiles(id) on delete set null,
 points integer not null check(points between 1 and 100),
 response text not null,
 created_at timestamptz not null default clock_timestamp(),
 read_at timestamptz
);
alter table core_private.moderation_penalties enable row level security;
revoke all on core_private.moderation_penalties from public,anon,authenticated;

do $migration$
declare definition text;
begin
 definition:=pg_get_functiondef('core_private.reputation_score(uuid)'::regprocedure);
 if position('select least(100,50+' in definition)=0 then raise exception 'Unexpected reputation score'; end if;
 definition:=replace(definition,'select least(100,50+',
   'select greatest(0,least(100,50+');
 definition:=replace(definition,')::integer);',
   ')::integer) - coalesce((select sum(points) from core_private.moderation_penalties where target_id=target),0)::integer);');
 if position('moderation_penalties' in definition)=0 then raise exception 'Penalty score insertion failed'; end if;
 execute definition;
end $migration$;

create function public.core_moderation_penalize(args jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=core_private.account_guard(); r public.core_reports; target uuid; points integer; result jsonb;
 previous core_private.moderation_penalties;
begin
 if not exists(select 1 from public.core_moderators where user_id=actor and enabled and expires_at>clock_timestamp()) then
  raise exception using errcode='PT403',message='moderator_required'; end if;
 if jsonb_typeof(args) is distinct from 'object' or args-array['report_id','expected_version','points','public_response','internal_note','request_key']<>'{}'
  or jsonb_typeof(args->'points') is distinct from 'number' or coalesce(args->>'points','') !~ '^[1-9][0-9]{0,2}$'
  or (args->>'points')::integer>100 then raise exception using errcode='PT422',message='invalid_moderation_input'; end if;
 select * into r from public.core_reports where id=(args->>'report_id')::uuid for update;
 if not found then raise exception using errcode='PT404',message='report_not_found'; end if;
 if r.payload->>'kind'='user' then target:=(r.payload->>'target_id')::uuid;
 elsif r.payload->>'kind'='post' then select author_id into target from public.core_posts where id=(r.payload->>'target_id')::uuid;
 elsif r.payload->>'kind'='message' then select sender_id into target from public.core_chat_messages where id=(r.payload->>'target_id')::uuid;
 end if;
 if target is null or target=r.owner_id or not exists(select 1 from public.core_profiles where id=target) then
  raise exception using errcode='PT422',message='report_target_unavailable'; end if;
 perform 1 from public.core_profiles where id=target for update;
 points:=(args->>'points')::integer;
 result:=public.core_moderation('review',jsonb_build_object('report_id',r.id,'expected_version',args->'expected_version',
  'action','resolve','public_response',args->'public_response','internal_note',args->'internal_note','request_key',args->'request_key'));
 select * into previous from core_private.moderation_penalties where report_id=r.id;
 if found then
  if previous.points<>points or previous.target_id<>target then raise exception using errcode='PT409',message='moderation_key_conflict'; end if;
  return result||jsonb_build_object('points_deducted',previous.points,'notification_status','in_app');
 end if;
 if points>core_private.reputation_score(target) then raise exception using errcode='PT422',message='penalty_exceeds_score'; end if;
 insert into core_private.moderation_penalties(report_id,target_id,moderator_id,points,response)
 values(r.id,target,actor,points,coalesce(args->>'public_response','')) on conflict(report_id) do nothing;
 return result||jsonb_build_object('points_deducted',points,'notification_status','in_app');
exception when invalid_text_representation or numeric_value_out_of_range then
 raise exception using errcode='PT422',message='invalid_moderation_input';
end $$;
revoke all on function public.core_moderation_penalize(jsonb) from public,anon;
grant execute on function public.core_moderation_penalize(jsonb) to authenticated;

-- Only the affected account can read or mark its own notices.
create function public.core_penalty_notices(action text,args jsonb default '{}') returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=core_private.account_guard(); items jsonb;
begin
 if action='list' and args='{}'::jsonb then
  select coalesce(jsonb_agg(jsonb_build_object('id',p.id,'points',p.points,'created_at',p.created_at,
   'read_at',p.read_at,'response',p.response) order by p.created_at desc),'[]'::jsonb)
   into items from (select * from core_private.moderation_penalties where target_id=actor order by created_at desc limit 50) p;
  return jsonb_build_object('items',items);
 elsif action='read' and args-array['id']='{}'::jsonb then
  update core_private.moderation_penalties set read_at=coalesce(read_at,clock_timestamp())
   where id=(args->>'id')::uuid and target_id=actor;
  if not found then raise exception using errcode='PT404',message='notice_not_found'; end if;
  return jsonb_build_object('ok',true);
 end if;
 raise exception using errcode='PT422',message='invalid_notification_input';
exception when invalid_text_representation then
 raise exception using errcode='PT422',message='invalid_notification_input';
end $$;
revoke all on function public.core_penalty_notices(text,jsonb) from public,anon;
grant execute on function public.core_penalty_notices(text,jsonb) to authenticated;

create function public.core_moderation_penalty_target(report_id uuid) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare actor uuid:=core_private.account_guard(); r public.core_reports; target uuid; target_name text;
begin
 if not exists(select 1 from public.core_moderators where user_id=actor and enabled and expires_at>clock_timestamp()) then
  raise exception using errcode='PT403',message='moderator_required'; end if;
 select * into r from public.core_reports where id=report_id;
 if not found then raise exception using errcode='PT404',message='report_not_found'; end if;
 if r.payload->>'kind'='user' then target:=(r.payload->>'target_id')::uuid;
 elsif r.payload->>'kind'='post' then select author_id into target from public.core_posts where id=(r.payload->>'target_id')::uuid;
 elsif r.payload->>'kind'='message' then select sender_id into target from public.core_chat_messages where id=(r.payload->>'target_id')::uuid;
 end if;
 if target is null or target=r.owner_id then return jsonb_build_object('available',false); end if;
 select display_name into target_name from public.core_profiles where id=target;
 if not found then return jsonb_build_object('available',false); end if;
 return jsonb_build_object('available',true,'name',target_name,'score',core_private.reputation_score(target));
exception when invalid_text_representation then
 return jsonb_build_object('available',false);
end $$;
revoke all on function public.core_moderation_penalty_target(uuid) from public,anon;
grant execute on function public.core_moderation_penalty_target(uuid) to authenticated;
