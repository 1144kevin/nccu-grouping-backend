-- Validate malformed direct RPC input without exposing database errors.
create or replace function core_private.dispatch(action text, args jsonb) returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '8s' as $$
declare actor uuid := auth.uid(); post public.core_posts; request public.core_join_requests;
  v_payload jsonb; result jsonb; target_post_id uuid; n integer; expected integer; profile public.core_profiles;
  scope text; term text; last_time timestamptz; last_id uuid; page_size integer; next_cursor text; allowed text[];
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false)) or
    not exists(select 1 from auth.sessions where id=(auth.jwt()->>'session_id')::uuid and user_id=actor
      and (not_after is null or not_after>clock_timestamp()))
  then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  if jsonb_typeof(args) is distinct from 'object' then
    raise exception using errcode='PT422',message='invalid_input'; end if;
  allowed := case action
    when 'me' then array[]::text[] when 'profile' then array['display_name','bio','badge','expected_version']
    when 'posts' then array['scope','limit','cursor','q','active_only']
    when 'create' then array['value','request_key'] when 'edit' then array['post_id','value','expected_version']
    when 'post' then array['post_id'] when 'join' then array['post_id','expected_version']
    when 'requests' then array['post_id'] when 'decide' then array['join_id','status']
    when 'withdraw' then array['post_id'] when 'cancel' then array['post_id'] else null end;
  if allowed is null or args - allowed <> '{}'::jsonb then
    raise exception using errcode='PT422',message='invalid_input'; end if;
  insert into public.core_profiles(id,display_name)
    select id,coalesce(nullif(left(btrim(raw_user_meta_data->>'display_name'),40),''),'新朋友')
    from auth.users where id=actor on conflict(id) do nothing;

  if action='me' then return core_private.profile_view(actor); end if;
  if action='profile' then
    if jsonb_typeof(args->'display_name') is distinct from 'string' or
      jsonb_typeof(args->'bio') is distinct from 'string' or
      jsonb_typeof(args->'badge') is distinct from 'string' or
      jsonb_typeof(args->'expected_version') is distinct from 'number' or
      (args->>'expected_version') !~ '^[1-9][0-9]*$'
    then raise exception using errcode='PT422',message='invalid_profile'; end if;
    update public.core_profiles set display_name=btrim(args->>'display_name'),bio=btrim(args->>'bio'),
      badge=args->>'badge',version=version+1 where id=actor and version=(args->>'expected_version')::integer
      returning * into profile;
    if not found then raise exception using errcode='PT409',message='profile_changed_reload'; end if;
    return core_private.profile_view(actor);
  end if;
  if action='posts' then
    scope:=coalesce(args->>'scope','feed'); term:=btrim(coalesce(args->>'q','')); page_size:=coalesce((args->>'limit')::integer,30);
    if scope not in ('feed','hosted','participating') or page_size not between 1 and 50 or char_length(term)>100 then
      raise exception using errcode='PT422',message='invalid_query'; end if;
    if coalesce(args->>'cursor','')<>'' then
      v_payload:=convert_from(decode(args->>'cursor','base64'),'UTF8')::jsonb;
      last_time:=(v_payload->>0)::timestamptz; last_id:=(v_payload->>1)::uuid;
      if last_time is null or last_id is null or jsonb_array_length(v_payload)<>2 then
        raise exception using errcode='PT422',message='invalid_cursor'; end if;
    end if;
    select coalesce(jsonb_agg(core_private.post_view(p.id,actor) order by p.created_at desc,p.id desc),'[]') into result from (
      select p.* from public.core_posts p join public.core_profiles u on u.id=p.author_id
      where (scope='feed' or (scope='hosted' and p.author_id=actor) or (scope='participating' and exists(
        select 1 from public.core_join_requests j where j.post_id=p.id and j.requester_id=actor and j.status in ('pending','accepted'))))
      and (term='' or strpos(lower(p.payload->>'content'),lower(term))>0 or
        strpos(lower((p.payload->'tags')::text),lower(term))>0 or strpos(lower(u.display_name),lower(term))>0)
      and (not coalesce((args->>'active_only')::boolean,false) or (not p.cancelled and p.deadline_at>clock_timestamp()))
      and (last_time is null or (p.created_at,p.id)<(last_time,last_id))
      order by p.created_at desc,p.id desc limit page_size+1
    ) p;
    if jsonb_array_length(result)>page_size then
      v_payload:=result->(page_size-1);
      next_cursor:=replace(encode(convert_to(jsonb_build_array(v_payload->>'created_at',v_payload->>'id')::text,'UTF8'),'base64'),E'\n','');
      result:=result-page_size;
    end if;
    return jsonb_build_object('items',result,'next_cursor',next_cursor);
  end if;
  if action='create' then
    if jsonb_typeof(args->'request_key') is distinct from 'string' or char_length(coalesce(args->>'request_key','')) not between 12 and 100 then
      raise exception using errcode='PT422',message='invalid_request_key'; end if;
    -- Serialize matching create retries by owner, then enforce a unique owner/key pair.
    perform 1 from public.core_profiles where id=actor for update;
    select * into post from public.core_posts where author_id=actor and request_key=args->>'request_key';
    if found then
      if post.original_payload is distinct from args->'value' then
        raise exception using errcode='PT409',message='idempotency_key_payload_conflict'; end if;
      return core_private.post_view(post.id,actor);
    end if;
    v_payload:=core_private.post_input(args->'value');
    insert into public.core_posts(author_id,payload,deadline_at,capacity,request_key,original_payload)
      values(actor,v_payload,(v_payload->>'deadline_at')::timestamptz,(v_payload->>'capacity')::integer,args->>'request_key',args->'value')
      returning * into post;
    return core_private.post_view(post.id,actor);
  end if;
  if action='decide' then
    select * into request from public.core_join_requests where id=(args->>'join_id')::uuid;
    if not found then raise exception using errcode='PT404',message='join_request_not_found'; end if;
    target_post_id:=request.post_id;
  else target_post_id:=(args->>'post_id')::uuid;
  end if;
  -- Every membership / edit operation locks the same parent row before inspecting children.
  select * into post from public.core_posts where id=target_post_id for update;
  if not found then raise exception using errcode='PT404',message='post_not_found'; end if;
  if action='post' then return core_private.post_view(post.id,actor); end if;
  if action in ('edit','requests','decide','cancel') and post.author_id<>actor then
    raise exception using errcode='PT403',message='host_only'; end if;
  if action='requests' then
    select coalesce(jsonb_agg(core_private.join_view(j) order by j.created_at,j.id),'[]') into result
      from public.core_join_requests j where j.post_id=post.id;
    return result;
  end if;
  if action='cancel' and post.cancelled then return core_private.post_view(post.id,actor); end if;
  if action='decide' then
    select * into request from public.core_join_requests where id=request.id;
    if args->>'status' not in ('accepted','rejected') or args->>'status' is null then
      raise exception using errcode='PT422',message='invalid_decision'; end if;
    if request.status=args->>'status' then return core_private.join_view(request); end if;
  end if;
  if action='withdraw' then
    if post.author_id=actor then raise exception using errcode='PT409',message='host_must_cancel_post'; end if;
    select * into request from public.core_join_requests where post_id=post.id and requester_id=actor;
    if not found or request.status in ('cancelled','rejected') then return core_private.post_view(post.id,actor); end if;
  end if;
  if post.cancelled then raise exception using errcode='PT409',message='post_cancelled'; end if;
  if post.deadline_at<=clock_timestamp() then raise exception using errcode='PT409',message='post_deadline_passed'; end if;
  if action in ('edit','join') then
    if jsonb_typeof(args->'expected_version') is distinct from 'number' or
      (args->>'expected_version') !~ '^[1-9][0-9]*$' then
      raise exception using errcode='PT422',message='invalid_version'; end if;
    expected:=(args->>'expected_version')::integer;
    if post.version<>expected then raise exception using errcode='PT409',message='post_changed_reload'; end if;
  end if;
  if action='edit' then
    if exists(select 1 from public.core_join_requests where post_id=post.id and status in ('pending','accepted')) then
      raise exception using errcode='PT409',message='post_has_active_requests'; end if;
    v_payload:=core_private.post_input(args->'value');
    update public.core_posts set payload=v_payload,deadline_at=(v_payload->>'deadline_at')::timestamptz,
      capacity=(v_payload->>'capacity')::integer,version=version+1 where id=post.id returning * into post;
    return core_private.post_view(post.id,actor);
  end if;
  select 1+count(*) into n from public.core_join_requests where post_id=post.id and status='accepted';
  if action='join' then
    if post.author_id=actor then raise exception using errcode='PT409',message='host_already_joined'; end if;
    select * into request from public.core_join_requests where post_id=post.id and requester_id=actor;
    if found and request.status in ('pending','accepted') then return core_private.join_view(request); end if;
    if n>=post.capacity then raise exception using errcode='PT409',message='post_full'; end if;
    insert into public.core_join_requests(post_id,requester_id,status) values(post.id,actor,'pending')
      on conflict(post_id,requester_id) do update set status='pending',created_at=clock_timestamp(),decided_at=null
      returning * into request;
    return core_private.join_view(request);
  elsif action='decide' then
    if request.status<>'pending' then raise exception using errcode='PT409',message='join_request_not_pending'; end if;
    if args->>'status'='accepted' and n>=post.capacity then raise exception using errcode='PT409',message='post_full'; end if;
    update public.core_join_requests set status=args->>'status',decided_at=clock_timestamp() where id=request.id returning * into request;
    return core_private.join_view(request);
  elsif action='withdraw' then
    update public.core_join_requests set status='cancelled',decided_at=clock_timestamp() where id=request.id;
  elsif action='cancel' then
    update public.core_posts set cancelled=true where id=post.id returning * into post;
  end if;
  return core_private.post_view(post.id,actor);
exception when data_exception or check_violation or not_null_violation then
  raise exception using errcode='PT422',message='invalid_input';
end;
$$;
