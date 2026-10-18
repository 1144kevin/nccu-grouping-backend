-- Self-service account deletion: explicit recent-session challenge, resumable Storage cleanup,
-- then one transaction removing application data and the actual Auth user.
create table core_private.account_deletion_jobs (
 owner_id uuid primary key references auth.users(id) on delete cascade,
 phase text not null check(phase in ('prepared','deleting')),
 session_id uuid not null,
 challenge_hash text not null,
 expires_at timestamptz not null,
 updated_at timestamptz not null default clock_timestamp()
);
alter table core_private.account_deletion_jobs enable row level security;
revoke all on core_private.account_deletion_jobs from public,anon,authenticated;
create policy account_deletion_jobs_private on core_private.account_deletion_jobs for all to anon,authenticated using(false) with check(false);

create or replace function core_private.account_pending(actor uuid) returns boolean
language sql stable security definer set search_path='' as $$
 select exists(select 1 from core_private.account_deletion_jobs where owner_id=actor and phase='deleting');
$$;

create function core_private.deletion_counts(actor uuid) returns jsonb
language sql stable security definer set search_path='' as $$
 select jsonb_build_object(
 'owned_posts',(select count(*) from public.core_posts where author_id=actor or demo_owner_id=actor),
 'messages',(select count(*) from public.core_chat_messages where sender_id=actor),
 'images',(select count(*) from public.core_media_assets where owner_id=actor),
 'participations',(select count(*) from public.core_join_requests where requester_id=actor),
 'proposals',(select count(*) from public.core_stealth_proposals where actor=any(owners)));
$$;
revoke all on function core_private.deletion_counts(uuid) from public,anon,authenticated;

create function core_private.account_deletion(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=core_private.account_guard(true); job core_private.account_deletion_jobs;
 nonce text; recent boolean; result jsonb; affected uuid[];
begin
 if action not in ('status','prepare','begin','assets','finish') or jsonb_typeof(args) is distinct from 'object'
  or args-(case when action='begin' then array['confirmation','challenge'] else array[]::text[] end)<>'{}' then
  raise exception using errcode='PT422',message='invalid_account_deletion_input'; end if;
 select exists(select 1 from auth.sessions where user_id=actor and id::text=auth.jwt()->>'session_id'
   and created_at>=clock_timestamp()-interval '15 minutes') into recent;
 select * into job from core_private.account_deletion_jobs where owner_id=actor for update;
 if action='status' then return jsonb_build_object('phase',coalesce(job.phase,'none'),'recent_auth',recent,'counts',core_private.deletion_counts(actor)); end if;
 if action in ('prepare','begin') and not recent then
  raise exception using errcode='PT403',message='account_deletion_reauthentication_required'; end if;
 if action='prepare' then
  if job.phase='deleting' then raise exception using errcode='PT409',message='account_deletion_pending'; end if;
  nonce:=translate(rtrim(encode(extensions.gen_random_bytes(32),'base64'),'='),'+/','-_');
  insert into core_private.account_deletion_jobs(owner_id,phase,session_id,challenge_hash,expires_at)
   values(actor,'prepared',(auth.jwt()->>'session_id')::uuid,encode(extensions.digest(nonce,'sha256'),'hex'),clock_timestamp()+interval '10 minutes')
   on conflict(owner_id) do update set phase='prepared',session_id=excluded.session_id,challenge_hash=excluded.challenge_hash,
    expires_at=excluded.expires_at,updated_at=clock_timestamp();
  return jsonb_build_object('phase','prepared','challenge',nonce,'expires_in',600,'counts',core_private.deletion_counts(actor));
 elsif action='begin' then
  if args->>'confirmation' is distinct from 'DELETE' or job.owner_id is null then
   raise exception using errcode='PT422',message='explicit_account_deletion_required'; end if;
  if job.phase='prepared' and (job.expires_at<=clock_timestamp() or job.session_id::text is distinct from auth.jwt()->>'session_id'
    or jsonb_typeof(args->'challenge') is distinct from 'string' or length(args->>'challenge')<>43
    or job.challenge_hash is distinct from encode(extensions.digest(args->>'challenge','sha256'),'hex')) then
   raise exception using errcode='PT409',message='account_deletion_challenge_expired'; end if;
  perform 1 from auth.users where id=actor for update;
  update core_private.account_deletion_jobs set phase='deleting',updated_at=clock_timestamp() where owner_id=actor;
  return jsonb_build_object('phase','deleting');
 end if;
 if job.phase is distinct from 'deleting' then raise exception using errcode='PT409',message='account_deletion_not_started'; end if;
 if action='assets' then
  select coalesce(jsonb_agg(jsonb_build_object('path',a.owner_id::text||'/'||a.id::text||'.jpg')),'[]') into result
   from public.core_media_assets a where a.owner_id=actor;
  return jsonb_build_object('items',result);
 end if;
 -- Never delete storage.objects rows by SQL: the backend must remove actual blobs first.
 if exists(select 1 from storage.objects where owner_id=actor::text) then
  raise exception using errcode='PT409',message='account_deletion_storage_remaining'; end if;
 perform 1 from auth.users where id=actor for update;
 select array_agg(distinct post_id) into affected from public.core_join_requests where requester_id=actor;
 -- Hold the global guard lock and Auth row lock until commit. Removing the private job here
 -- permits our cleanup; other requests cannot observe this intermediate state.
 delete from core_private.account_deletion_jobs where owner_id=actor;
 -- Remove system-generated role-name summaries from surviving formed posts.
 update public.core_posts p set
  payload=jsonb_set(p.payload,'{content}',to_jsonb(regexp_replace(p.payload->>'content',E'\n下單：[^\n]*\n配合時段：',E'\n下單／取餐：成員帳號已刪除，請重新協調\n配合時段：'))),
  original_payload=jsonb_set(p.original_payload,'{content}',to_jsonb(regexp_replace(p.original_payload->>'content',E'\n下單：[^\n]*\n配合時段：',E'\n下單／取餐：成員帳號已刪除，請重新協調\n配合時段：'))),
  version=p.version+1
 where p.author_id<>actor and p.id in (select formed_post_id from public.core_stealth_proposals where actor=any(owners));
 delete from public.core_reports where owner_id=actor or payload->>'target_id'=actor::text
  or payload->>'target_id' in (select id::text from public.core_posts where author_id=actor or demo_owner_id=actor)
  or payload->>'target_id' in (select id::text from public.core_chat_messages where sender_id=actor);
 delete from public.core_stealth_proposals where actor=any(owners) or terms_author_id=actor
  or formed_post_id in (select id from public.core_posts where author_id=actor or demo_owner_id=actor);
 delete from public.core_stealth_signals where owner_id=actor;
 delete from public.core_stealth_agents where owner_id=actor;
 delete from public.core_chat_reads where viewer_id=actor;
 delete from public.core_chat_messages where sender_id=actor;
 delete from public.core_join_requests where requester_id=actor or post_id in (select id from public.core_posts where author_id=actor or demo_owner_id=actor);
 update public.core_posts set version=version+1 where id=any(affected) and author_id<>actor;
 delete from public.core_posts where author_id=actor or demo_owner_id=actor;
 delete from public.core_post_images where asset_id in (select id from public.core_media_assets where owner_id=actor);
 delete from public.core_media_assets where owner_id=actor;
 delete from core_private.demo_batches where owner_id=actor;
 -- Reports targeting removed private content cannot retain that removed user's identifying details.
 delete from public.core_reports where owner_id=actor or payload->>'target_id'=actor::text;
 delete from public.core_profiles where id=actor;
 delete from auth.refresh_tokens where user_id=actor::text;
 delete from auth.sessions where user_id=actor;
 delete from auth.users where id=actor;
 return jsonb_build_object('deleted',true);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range then
 raise exception using errcode='PT422',message='invalid_account_deletion_input';
end $$;
revoke all on function core_private.account_deletion(text,jsonb) from public,anon,authenticated;
grant execute on function core_private.account_deletion(text,jsonb) to authenticated;
create function public.core_account_deletion(action text,args jsonb default '{}') returns jsonb
language sql security invoker set search_path='' as $$ select core_private.account_deletion(action,args) $$;
revoke all on function public.core_account_deletion(text,jsonb) from public,anon,authenticated;
grant execute on function public.core_account_deletion(text,jsonb) to authenticated;

-- Retain the existing helper's OID so Storage RLS continues using this replacement.
do $$ begin
 execute replace(pg_get_functiondef('core_private.media_access(text,text)'::regprocedure),
  'core_private.media_access(', 'core_private.media_access_before_account_deletion(');
end $$;
revoke all on function core_private.media_access_before_account_deletion(text,text) from public,anon,authenticated;
create or replace function core_private.media_access(operation text,object_name text) returns boolean
language plpgsql security definer set search_path='' as $$
declare actor uuid:=core_private.media_actor(); asset public.core_media_assets;
begin
 if actor is null then return false; end if;
 if core_private.account_pending(actor) then
  if operation<>'delete' then return false; end if;
  perform pg_advisory_xact_lock(hashtextextended('core-account-safety-v1',0));
  select * into asset from public.core_media_assets a where a.owner_id=actor
   and object_name=a.owner_id::text||'/'||a.id::text||'.jpg' for update;
  return found;
 end if;
 return core_private.media_access_before_account_deletion(operation,object_name);
end $$;
