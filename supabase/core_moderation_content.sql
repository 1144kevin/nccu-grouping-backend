-- Private audit retained; public chat and previews use a placeholder.
alter table public.core_chat_messages add column moderation_hidden boolean not null default false;
create function public.core_moderation_context(report_id uuid) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=core_private.account_guard(); r public.core_reports; result jsonb;
begin
  if not exists(select 1 from public.core_moderators where user_id=actor and enabled and expires_at>clock_timestamp()) then
    raise exception using errcode='PT403',message='moderator_required'; end if;
  select * into r from public.core_reports where id=report_id;
  if not found then raise exception using errcode='PT404',message='report_not_found'; end if;
  if r.payload->>'kind'='message' then
    select jsonb_build_object('kind','message','target_id',m.id,'content',m.content,'hidden',m.moderation_hidden)
      into result from public.core_chat_messages m where m.id::text=r.payload->>'target_id';
  elsif r.payload->>'kind'='post' then
    select jsonb_build_object('kind','post','target_id',p.id,'content',p.payload->>'content','cancelled',p.cancelled)
      into result from public.core_posts p where p.id::text=r.payload->>'target_id';
  end if;
  return coalesce(result,jsonb_build_object('unavailable',true));
end; $$;
revoke all on function public.core_moderation_context(uuid) from public,anon;
grant execute on function public.core_moderation_context(uuid) to authenticated;
create or replace function core_private.chat_message_view(message public.core_chat_messages) returns jsonb
language sql stable set search_path = '' as $$
  select jsonb_build_object('id',message.id,'post_id',message.post_id,'sender_id',message.sender_id,
    'sender_name',p.display_name,'sender_badge',p.badge,'content',case when message.moderation_hidden then '此訊息已由管理者隱藏' else message.content end,'created_at',message.created_at)
  from public.core_profiles p where p.id=message.sender_id;
$$;
revoke all on function core_private.chat_message_view(public.core_chat_messages) from public,anon,authenticated;

create or replace function public.core_moderation(action text,args jsonb default '{}') returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=auth.uid(); r public.core_reports; e public.core_moderation_events;
  result jsonb; state text; operation text; post_id uuid; affected integer;
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false)
    and (banned_until is null or banned_until<clock_timestamp()))
    or not exists(select 1 from auth.sessions where user_id=actor and id=(auth.jwt()->>'session_id')::uuid)
    then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  if jsonb_typeof(args) is distinct from 'object' then
    raise exception using errcode='PT422',message='invalid_moderation_input'; end if;
  if action='mine' and args='{}' then
    select coalesce(jsonb_agg(payload||jsonb_build_object('id',id,'status',status,'created_at',created_at,
      'public_response',public_response,'review_version',review_version) order by created_at desc,id desc),'[]')
      into result from (select * from public.core_reports where owner_id=actor order by created_at desc,id desc limit 50) q;
    return jsonb_build_object('items',result);
  end if;
  -- Never trust client-editable user_metadata, email domains, or caller-provided actor IDs.
  if not exists(select 1 from public.core_moderators where user_id=actor and enabled and expires_at>clock_timestamp())
    then raise exception using errcode='PT403',message='moderator_required'; end if;
  if action='list' and args='{}' then
    select coalesce(jsonb_agg(to_jsonb(q) order by created_at desc,id desc),'[]') into result
      from(select id,owner_id,payload,status,review_version,public_response,created_at from public.core_reports
        order by created_at desc,id desc limit 50) q;
    return jsonb_build_object('items',result);
  end if;
  if action<>'review' or args-array['report_id','expected_version','action','public_response','internal_note','request_key']<>'{}'
    or jsonb_typeof(args->'expected_version') is distinct from 'number' or args->>'expected_version' !~ '^[1-9][0-9]{0,8}$'
    or not coalesce(args->>'action' in ('review','resolve','dismiss','cancel_post','hide_message'),false)
    or jsonb_typeof(args->'public_response') is distinct from 'string'
    or char_length(btrim(args->>'public_response')) not between 5 and 300
    or jsonb_typeof(args->'internal_note') is distinct from 'string' or char_length(args->>'internal_note')>1000
    or not coalesce(args->>'request_key' ~ '^[0-9a-fA-F-]{36}$',false)
    or not coalesce(args->>'report_id' ~ '^[0-9a-fA-F-]{36}$',false)
    then raise exception using errcode='PT422',message='invalid_moderation_input'; end if;
  -- Lock report before replay lookup: concurrent identical retries return the same result.
  perform pg_advisory_xact_lock(hashtextextended(args->>'request_key',0));
  select * into r from public.core_reports where id=(args->>'report_id')::uuid for update;
  if not found then raise exception using errcode='PT404',message='report_not_found'; end if;
  select * into e from public.core_moderation_events where request_key=(args->>'request_key')::uuid;
  if found then
    if e.actor_id is distinct from actor or e.request<>args then
      raise exception using errcode='PT409',message='moderation_key_conflict'; end if;
    return e.result;
  end if;
  if r.review_version<>(args->>'expected_version')::integer then
    raise exception using errcode='PT409',message='report_version_conflict'; end if;
  operation:=args->>'action';
  if r.status in ('resolved','dismissed') or (operation='review' and r.status<>'received') then
    raise exception using errcode='PT409',message='report_already_reviewed'; end if;
  if operation<>'review' and r.status<>'reviewing' then
    raise exception using errcode='PT409',message='review_required'; end if;
  if operation='cancel_post' then
    if r.payload->>'kind'<>'post' then raise exception using errcode='PT422',message='post_report_required'; end if;
    post_id:=(r.payload->>'target_id')::uuid;
    update public.core_posts set cancelled=true,version=version+1 where id=post_id and not cancelled;
    get diagnostics affected=row_count;
    if affected<>1 then raise exception using errcode='PT409',message='post_unavailable'; end if;
  end if;
  if operation='hide_message' then
    if r.payload->>'kind'<>'message' then raise exception using errcode='PT422',message='message_report_required'; end if;
    update public.core_chat_messages set moderation_hidden=true where id=(r.payload->>'target_id')::uuid and not moderation_hidden;
    get diagnostics affected=row_count;
    if affected<>1 then raise exception using errcode='PT409',message='message_unavailable'; end if;
  end if;
  state:=case operation when 'review' then 'reviewing' when 'dismiss' then 'dismissed' else 'resolved' end;
  update public.core_reports set status=state,public_response=btrim(args->>'public_response'),
    review_version=review_version+1 where id=r.id returning * into r;
  result:=jsonb_build_object('id',r.id,'status',r.status,'review_version',r.review_version,
    'public_response',r.public_response,'cancelled_post_id',post_id);
  insert into public.core_moderation_events(report_id,actor_id,request_key,request,result)
    values(r.id,actor,(args->>'request_key')::uuid,args,result);
  return result;
end; $$;
revoke all on function public.core_moderation(text,jsonb) from public,anon;
grant execute on function public.core_moderation(text,jsonb) to authenticated;
