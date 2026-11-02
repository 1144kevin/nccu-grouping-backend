-- Private profile/evidence images, profile likes and completion-based reputation.
create table public.core_personal_images (like public.core_media_assets including all);
alter table public.core_personal_images add foreign key(owner_id) references public.core_profiles(id) on delete cascade;
alter table public.core_personal_images add purpose text not null check(purpose in ('avatar','evidence'));
alter table public.core_personal_images enable row level security;
revoke all on public.core_personal_images from public,anon,authenticated;
create policy personal_images_rpc_only on public.core_personal_images for all to authenticated using(false) with check(false);
alter table public.core_profiles add avatar_id uuid references public.core_personal_images(id) on delete set null;
create table public.core_profile_likes (
 actor_id uuid not null references public.core_profiles(id) on delete cascade,
 target_id uuid not null references public.core_profiles(id) on delete cascade,
 created_at timestamptz not null default clock_timestamp(),
 primary key(actor_id,target_id), check(actor_id<>target_id)
);
create index core_profile_likes_target on public.core_profile_likes(target_id);
alter table public.core_profile_likes enable row level security;
revoke all on public.core_profile_likes from public,anon,authenticated;
create policy profile_likes_rpc_only on public.core_profile_likes for all to authenticated using(false) with check(false);
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
 values('core-personal-images','core-personal-images',false,4194304,array['image/jpeg']);

create function core_private.personal_image_access(operation text,object_name text) returns boolean
language plpgsql security definer set search_path='' as $$
declare actor uuid:=core_private.media_actor(); asset public.core_personal_images; attached boolean;
begin
 if actor is null then return false; end if;
 perform pg_advisory_xact_lock(hashtextextended('core-account-safety-v1',0));
 select * into asset from public.core_personal_images where object_name=id::text||'.jpg' for update;
 if not found then return false; end if;
 attached:=exists(select 1 from public.core_profiles where avatar_id=asset.id)
   or exists(select 1 from public.core_reports where payload->>'evidence_image_id'=asset.id::text);
 if core_private.account_pending(actor) then return operation='delete' and asset.owner_id=actor; end if;
 return case operation
 when 'insert' then asset.owner_id=actor and asset.state='pending'
 when 'select' then asset.owner_id=actor or (asset.state='ready' and (
   (asset.purpose='avatar' and exists(select 1 from public.core_profiles where avatar_id=asset.id)
    and not core_private.users_blocked(actor,asset.owner_id) and not core_private.account_pending(asset.owner_id))
   or (asset.purpose='evidence' and attached and exists(select 1 from public.core_moderators
     where user_id=actor and enabled and expires_at>clock_timestamp()))))
 when 'delete' then asset.owner_id=actor and not attached
 else false end;
end $$;
revoke all on function core_private.personal_image_access(text,text) from public,anon;
grant execute on function core_private.personal_image_access(text,text) to authenticated;
create policy personal_images_insert on storage.objects for insert to authenticated with check
 (bucket_id='core-personal-images' and owner_id=auth.uid()::text and core_private.personal_image_access('insert',name));
create policy personal_images_select on storage.objects for select to authenticated using
 (bucket_id='core-personal-images' and core_private.personal_image_access('select',name));
create policy personal_images_delete on storage.objects for delete to authenticated using
 (bucket_id='core-personal-images' and core_private.personal_image_access('delete',name));

create function core_private.profile_social(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare actor uuid:=core_private.account_guard(); target uuid; asset uuid; total bigint; liked boolean;
begin
 if jsonb_typeof(args) is distinct from 'object' or args-array['user_id','liked']<>'{}'::jsonb
   or action not in ('get','like','avatar') then raise exception using errcode='PT422',message='invalid_profile_input'; end if;
 target:=(args->>'user_id')::uuid;
 if target is null or not exists(select 1 from public.core_profiles where id=target)
   or core_private.users_blocked(actor,target) or core_private.account_pending(target) then
   raise exception using errcode='PT404',message='public_profile_not_found'; end if;
 if action='avatar' then
   select avatar_id into asset from public.core_profiles where id=target;
   if asset is null then return jsonb_build_object('image_id',null); end if;
   return jsonb_build_object('image_id',asset);
 end if;
 if action='like' then
   if target=actor or jsonb_typeof(args->'liked') is distinct from 'boolean' then
     raise exception using errcode='PT422',message='invalid_profile_like'; end if;
   perform pg_advisory_xact_lock(hashtextextended('profile-likes:'||target::text,0));
   if (args->>'liked')::boolean then
     insert into public.core_profile_likes(actor_id,target_id) values(actor,target) on conflict do nothing;
   else delete from public.core_profile_likes where actor_id=actor and target_id=target; end if;
 end if;
 select count(*) into total from public.core_profile_likes where target_id=target
   and not core_private.users_blocked(actor_id,target_id) and not core_private.account_pending(actor_id);
 select exists(select 1 from public.core_profile_likes where actor_id=actor and target_id=target) into liked;
 return jsonb_build_object('likes',total,'liked',liked);
exception when invalid_text_representation then raise exception using errcode='PT422',message='invalid_profile_input';
end $$;
revoke all on function core_private.profile_social(text,jsonb) from public,anon,authenticated;
create function public.core_profile_social(action text,args jsonb default '{}'::jsonb) returns jsonb
language sql set search_path='' as $$select core_private.profile_social(action,args)$$;
revoke all on function public.core_profile_social(text,jsonb) from public,anon;
grant execute on function public.core_profile_social(text,jsonb) to authenticated;
grant execute on function core_private.profile_social(text,jsonb) to authenticated;

create function core_private.reputation_score(target uuid) returns integer
language sql stable security definer set search_path='' as $$
 select least(100,coalesce((select sum(case when p.author_id=target then 5 else 3 end)
   from public.core_order_completions c join public.core_posts p on p.id=c.post_id
   where target=any(c.member_ids) and not p.cancelled and p.demo_batch is null),0)::integer
   +(select count(*)/10 from public.core_profile_likes l where l.target_id=target
     and not core_private.users_blocked(l.actor_id,l.target_id) and not core_private.account_pending(l.actor_id))::integer);
$$;
revoke all on function core_private.reputation_score(uuid) from public,anon,authenticated;
create or replace function core_private.reputation_level(target uuid) returns integer
language sql stable security definer set search_path='' as $$
 select greatest(1,least(5,(greatest(1,core_private.reputation_score(target))+19)/20));
$$;
CREATE OR REPLACE FUNCTION core_private.personal_media(action text, args jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
 SET statement_timeout TO '8s'
AS $function$
declare actor uuid:=core_private.account_guard(); asset public.core_personal_images; result jsonb;
  allowed text[]; linked uuid; object_path text; metadata jsonb; saved_image uuid; storage_id uuid;
  expected_proof text; signing_secret bytea;
begin
  if actor is null then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  allowed:=case action when 'reserve' then array['request_key','sha256','width','height','purpose']
    when 'ready' then array['image_id','storage_id','proof'] when 'snapshot' then array['image_id'] when 'read' then array['image_id']
    when 'delete_prepare' then array['image_id'] when 'delete_finish' then array['image_id']
     when 'list' then array[]::text[] else null end;
  if allowed is null or jsonb_typeof(args) is distinct from 'object' or args-allowed<>'{}'::jsonb then
    raise exception using errcode='PT422',message='invalid_image_input'; end if;
  if action='list' then
    select coalesce(jsonb_agg(jsonb_build_object('id',a.id,'width',a.width,'height',a.height,
      'state',a.state,'created_at',a.created_at) order by a.created_at desc),'[]') into result
      from public.core_personal_images a where a.owner_id=actor
      and not exists(select 1 from public.core_profiles where avatar_id=a.id)
      and not exists(select 1 from public.core_reports where payload->>'evidence_image_id'=a.id::text);
    return jsonb_build_object('items',result);
  end if;
  if action='reserve' then
    if not coalesce(args->>'purpose' in ('avatar','evidence'),false) or jsonb_typeof(args->'request_key') is distinct from 'string' or char_length(args->>'request_key') not between 12 and 100
      or jsonb_typeof(args->'sha256') is distinct from 'string' or (args->>'sha256') !~ '^[0-9a-f]{64}$'
      or jsonb_typeof(args->'width') is distinct from 'number' or (args->>'width') !~ '^[0-9]+$'
      or jsonb_typeof(args->'height') is distinct from 'number' or (args->>'height') !~ '^[0-9]+$'
      or (args->>'width')::integer not between 1 and 1600 or (args->>'height')::integer not between 1 and 1600 then
      raise exception using errcode='PT422',message='invalid_image_input'; end if;
    perform pg_advisory_xact_lock(hashtextextended('core-media-reservations',0));
    select * into asset from public.core_personal_images where owner_id=actor and request_key=args->>'request_key';
    if found then
      if asset.purpose<>args->>'purpose' or asset.sha256<>args->>'sha256' or asset.width<>(args->>'width')::integer or asset.height<>(args->>'height')::integer then
        raise exception using errcode='PT409',message='image_key_conflict'; end if;
    else
      -- Image counts are unrestricted; Storage capacity and byte limits still apply.
      insert into public.core_personal_images(owner_id,request_key,sha256,width,height,purpose)
        values(actor,args->>'request_key',args->>'sha256',(args->>'width')::integer,(args->>'height')::integer,args->>'purpose') returning * into asset;
    end if;
  else
    select * into asset from public.core_personal_images where id=(args->>'image_id')::uuid for update;
    if not found then raise exception using errcode='PT404',message='image_not_found'; end if;
    if action='read' then
      if not core_private.personal_image_access('select',asset.id::text||'.jpg') then
        raise exception using errcode='PT404',message='image_not_found'; end if;
    elsif asset.owner_id<>actor then raise exception using errcode='PT404',message='image_not_found'; end if;
  end if;
  object_path:=asset.id::text||'.jpg';
  if action in ('snapshot','ready','publish','read') then
    select o.metadata,o.id into metadata,storage_id from storage.objects o where o.bucket_id='core-personal-images'
      and o.name=object_path and o.owner_id=asset.owner_id::text and not coalesce(o.is_delete_marker,false);
    if not found or metadata->>'mimetype' is distinct from 'image/jpeg'
      or coalesce((metadata->>'size')::bigint,0) not between 1 and 4194304 then
      raise exception using errcode='PT409',message='image_upload_incomplete'; end if;
  end if;
  if action='ready' then
    select secret into signing_secret from core_private.media_signing_keys where version=1;
    expected_proof:=encode(extensions.hmac(convert_to(asset.id::text||':'||actor::text||':'||asset.sha256||':'||storage_id::text,'UTF8'),signing_secret,'sha256'),'hex');
    if signing_secret is null or args->>'storage_id' is distinct from storage_id::text
      or jsonb_typeof(args->'proof') is distinct from 'string' or args->>'proof' is distinct from expected_proof then
      raise exception using errcode='PT403',message='image_verification_required'; end if;
    update public.core_personal_images set state='ready',verified_object_id=storage_id where id=asset.id;
    asset.state:='ready';
  elsif action='read' then
    if asset.state<>'ready' or asset.verified_object_id is distinct from storage_id then
      raise exception using errcode='PT409',message='image_upload_incomplete'; end if;
  elsif action in ('delete_prepare','delete_finish') then
    if not core_private.personal_image_access('delete',asset.id::text||'.jpg') then
      raise exception using errcode='PT409',message='image_already_published'; end if;
    if action='delete_finish' then
      if exists(select 1 from storage.objects where bucket_id='core-personal-images' and name=object_path) then
        raise exception using errcode='PT409',message='image_delete_incomplete'; end if;
      delete from public.core_personal_images where id=asset.id;
      return jsonb_build_object('deleted',true);
    end if;
  end if;
  return jsonb_build_object('id',asset.id,'object_path',object_path,'width',asset.width,'height',asset.height,'state',asset.state,
    'storage_id',case when action='snapshot' then storage_id else null end);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range then
  raise exception using errcode='PT422',message='invalid_image_input';
end;
$function$;
revoke all on function core_private.personal_media(text,jsonb) from public,anon,authenticated;
create function public.core_personal_media(action text,args jsonb default '{}'::jsonb) returns jsonb language sql set search_path='' as $$select core_private.personal_media(action,args)$$;
revoke all on function public.core_personal_media(text,jsonb) from public,anon;
grant execute on function public.core_personal_media(text,jsonb) to authenticated;
grant execute on function core_private.personal_media(text,jsonb) to authenticated;
CREATE OR REPLACE FUNCTION core_private.profile_view(actor uuid)
 RETURNS jsonb
 LANGUAGE sql
 STABLE
 SET search_path TO ''
AS $function$
 select jsonb_build_object('id',p.id,'display_name',p.display_name,'bio',p.bio,
 'avatar_id',p.avatar_id,'badge',p.badge,'version',p.version,'created_at',p.created_at,
 'email',u.email,'email_verified',u.email_confirmed_at is not null,'auth_mode','supabase',
 'profile_setup_required',p.profile_setup_required)
 from public.core_profiles p join auth.users u on u.id=p.id where p.id=actor;
$function$;
CREATE OR REPLACE FUNCTION core_private.dispatch(action text, args jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
 SET statement_timeout TO '8s'
AS $function$
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
  if action in ('join','withdraw','cancel','edit','decide') and exists(select 1 from public.core_order_completions where post_id=case when action='decide' then (select post_id from public.core_join_requests where id=(args->>'join_id')::uuid) else (args->>'post_id')::uuid end) then raise exception using errcode='PT409',message='order_already_completed'; end if;
  allowed := case action
    when 'me' then array[]::text[] when 'profile' then array['display_name','bio','badge','expected_version','avatar_id']
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
    if args->>'avatar_id' is not null and not exists(select 1 from public.core_personal_images where id=(args->>'avatar_id')::uuid and owner_id=actor and purpose='avatar' and state='ready') then
      raise exception using errcode='PT422',message='invalid_profile_avatar'; end if;
    perform 1 from public.core_personal_images where id=(args->>'avatar_id')::uuid for update;
    update public.core_profiles set avatar_id=case when args ? 'avatar_id' then (args->>'avatar_id')::uuid else avatar_id end,display_name=btrim(args->>'display_name'),bio=btrim(args->>'bio'),
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
$function$;
CREATE OR REPLACE FUNCTION core_private.engagement(action text, args jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
 SET statement_timeout TO '8s'
AS $function$
declare actor uuid:=auth.uid(); result jsonb; value jsonb; existing public.core_reports;
  prefs public.core_account_preferences; allowed text[]; tag jsonb; target uuid; parent uuid;
  keys text[]:=array['joinRequests','messages','recommendations','deadlineReminders','stealthInvites'];
  defaults jsonb:='{"notifications":{"joinRequests":true,"messages":true,"recommendations":false,"deadlineReminders":false,"stealthInvites":true},"preferred_tags":[]}';
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false))
    or not exists(select 1 from auth.sessions where id=(auth.jwt()->>'session_id')::uuid and user_id=actor
      and (not_after is null or not_after>clock_timestamp())) then
    raise exception using errcode='PT401',message='invalid_or_expired_session';
  end if;
  allowed:=case action
    when 'preferences_get' then array[]::text[] when 'preferences_put' then array['expected_version','value']
    when 'inbox_list' then array[]::text[] when 'inbox_put' then array['post_id','muted','archived']
    when 'reports' then array[]::text[] when 'report' then array['value','request_key'] else null end;
  if allowed is null or jsonb_typeof(args) is distinct from 'object' or args-allowed<>'{}'::jsonb then
    raise exception using errcode='PT422',message='invalid_account_input'; end if;
  if action in ('preferences_put','inbox_put','report') then
    perform pg_advisory_xact_lock(hashtextextended('core-engagement:'||actor::text,0));
  end if;
  if action in ('preferences_get','preferences_put') then
    select * into prefs from public.core_account_preferences where owner_id=actor;
    if action='preferences_get' then
      return coalesce(prefs.payload,defaults)||jsonb_build_object('version',coalesce(prefs.version,0));
    end if;
    value:=args->'value';
    if jsonb_typeof(args->'expected_version') is distinct from 'number'
      or (args->>'expected_version') !~ '^[0-9]{1,10}$'
      or jsonb_typeof(value) is distinct from 'object'
      or value-array['notifications','preferred_tags']<>'{}'::jsonb
      or jsonb_typeof(value->'notifications') is distinct from 'object'
      or (value->'notifications')-keys<>'{}'::jsonb
      or not (value->'notifications' ?& keys)
      or exists(select 1 from jsonb_each(value->'notifications') n where jsonb_typeof(n.value)<>'boolean')
      or jsonb_typeof(value->'preferred_tags') is distinct from 'array'
      or jsonb_array_length(value->'preferred_tags')>24 then
      raise exception using errcode='PT422',message='invalid_account_input'; end if;
    for tag in select * from jsonb_array_elements(value->'preferred_tags') loop
      if jsonb_typeof(tag)<>'string' or char_length(tag#>>'{}') not between 1 and 32 or (tag#>>'{}')<>btrim(tag#>>'{}') then
        raise exception using errcode='PT422',message='invalid_account_input'; end if;
    end loop;
    if (select count(distinct t#>>'{}') from jsonb_array_elements(value->'preferred_tags') t)
      <>jsonb_array_length(value->'preferred_tags') then
      raise exception using errcode='PT422',message='invalid_account_input'; end if;
    if (args->>'expected_version')::integer<>coalesce(prefs.version,0) then
      raise exception using errcode='PT409',message='preferences_changed_reload'; end if;
    if prefs.owner_id is not null and prefs.payload=value then
      return prefs.payload||jsonb_build_object('version',prefs.version); end if;
    insert into public.core_account_preferences(owner_id,payload,version)
      values(actor,value,1) on conflict(owner_id) do update
      set payload=excluded.payload,version=core_account_preferences.version+1,updated_at=clock_timestamp()
      returning * into prefs;
    return prefs.payload||jsonb_build_object('version',prefs.version);
  end if;
  if action='inbox_list' then
    select coalesce(jsonb_agg(jsonb_build_object('post_id',i.post_id,'muted',i.muted,'archived',i.archived)
      order by i.updated_at desc,i.post_id),'[]') into result
    from public.core_inbox_preferences i join public.core_posts p on p.id=i.post_id where i.owner_id=actor
      and (p.author_id=actor or exists(select 1 from public.core_join_requests j
        where j.post_id=p.id and j.requester_id=actor and j.status='accepted'));
    return jsonb_build_object('items',result);
  end if;
  if action='inbox_put' then
    target:=(args->>'post_id')::uuid;
    if jsonb_typeof(args->'muted') is distinct from 'boolean'
      or jsonb_typeof(args->'archived') is distinct from 'boolean' then
      raise exception using errcode='PT422',message='invalid_account_input'; end if;
    if not exists(select 1 from public.core_posts p where p.id=target and
      (p.author_id=actor or exists(select 1 from public.core_join_requests j
        where j.post_id=p.id and j.requester_id=actor and j.status='accepted'))) then
      raise exception using errcode='PT403',message='chat_members_only'; end if;
    if not exists(select 1 from public.core_inbox_preferences where owner_id=actor and post_id=target)
      and (select count(*) from public.core_inbox_preferences where owner_id=actor)>=200 then
      raise exception using errcode='PT409',message='inbox_preference_limit'; end if;
    insert into public.core_inbox_preferences(owner_id,post_id,muted,archived)
      values(actor,target,(args->>'muted')::boolean,(args->>'archived')::boolean)
      on conflict(owner_id,post_id) do update set muted=excluded.muted,archived=excluded.archived,
      updated_at=clock_timestamp();
    return jsonb_build_object('post_id',target,'muted',args->'muted','archived',args->'archived');
  end if;
  if action='reports' then
    select coalesce(jsonb_agg(r.payload||jsonb_build_object('id',r.id,'status',r.status,'created_at',r.created_at)
      order by r.created_at desc,r.id desc),'[]') into result
    from(select * from public.core_reports where owner_id=actor order by created_at desc,id desc limit 50) r;
    return jsonb_build_object('items',result);
  end if;
  value:=args->'value';
  if jsonb_typeof(args->'request_key') is distinct from 'string'
    or char_length(args->>'request_key') not between 12 and 100
    or jsonb_typeof(value) is distinct from 'object'
    or value-array['kind','target_id','reason','detail','platform','app_version','evidence_image_id']<>'{}'::jsonb
    or not coalesce(value->>'kind' in ('feedback','post','message','user'),false)
    or not coalesce(value->>'reason' in ('other','spam','harassment','inappropriate','fraud'),false)
    or not coalesce(value->>'platform' in ('web','ios','android','unknown'),false)
    or jsonb_typeof(value->'detail') is distinct from 'string'
    or char_length(btrim(value->>'detail')) not between 10 and 1000
    or jsonb_typeof(value->'app_version') is distinct from 'string'
    or char_length(value->>'app_version')>40 then
    raise exception using errcode='PT422',message='invalid_report_input'; end if;
  if value->>'evidence_image_id' is null then raise exception using errcode='PT422',message='report_image_required'; end if;
  perform 1 from public.core_personal_images where id=(value->>'evidence_image_id')::uuid and owner_id=actor and purpose='evidence' and state='ready' for update;
  if not found then raise exception using errcode='PT422',message='report_image_required'; end if;
  target:=(value->>'target_id')::uuid;
  if (value->>'kind'='feedback')<>(target is null) then
    raise exception using errcode='PT422',message='invalid_report_input'; end if;
  select * into existing from public.core_reports where owner_id=actor and request_key=args->>'request_key';
  if found then
    if existing.payload<>value then raise exception using errcode='PT409',message='report_key_conflict'; end if;
    return existing.payload||jsonb_build_object('id',existing.id,'status',existing.status,'created_at',existing.created_at);
  end if;
  if value->>'kind'='post' and not exists(select 1 from public.core_posts where id=target) then
    raise exception using errcode='PT404',message='report_target_unavailable'; end if;
  if value->>'kind'='user' and (target=actor or not exists(select 1 from public.core_profiles where id=target)) then
    raise exception using errcode='PT404',message='report_target_unavailable'; end if;
  if value->>'kind'='message' then
    select post_id into parent from public.core_chat_messages where id=target;
    if parent is null or not exists(select 1 from public.core_posts p where p.id=parent and
      (p.author_id=actor or exists(select 1 from public.core_join_requests j
        where j.post_id=p.id and j.requester_id=actor and j.status='accepted'))) then
      raise exception using errcode='PT404',message='report_target_unavailable'; end if;
  end if;
  if (select count(*) from public.core_reports where owner_id=actor
    and created_at>clock_timestamp()-interval '1 hour')>=5 then
    raise exception using errcode='PT429',message='report_rate_limit'; end if;
  insert into public.core_reports(owner_id,request_key,payload) values(actor,args->>'request_key',value)
    returning * into existing;
  return existing.payload||jsonb_build_object('id',existing.id,'status',existing.status,'created_at',existing.created_at);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range then
  raise exception using errcode='PT422',message='invalid_account_input';
end;
$function$;
CREATE OR REPLACE FUNCTION core_private.social(action text, args jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
 SET statement_timeout TO '8s'
AS $function$
declare actor uuid:=core_private.account_guard(); target uuid; k text; value jsonb; result jsonb; items jsonb; c jsonb;
 allowed text[]; page_size integer; last_msg public.core_direct_messages; d public.core_direct_threads; p public.core_posts;
 text_value text; key_value text; unread integer:=0; done integer; members jsonb; is_member boolean;
begin
 allowed:=case action
 when 'list' then array['kind','limit','cursor','deleted'] when 'preference' then array['kind','thread_id','pinned','inbox','deleted','muted']
 when 'start' then array['post_id'] when 'messages' then array['thread_id','limit','cursor']
 when 'send' then array['thread_id','content','client_key'] when 'read' then array['thread_id','message_id']
 when 'order' then array['post_id'] when 'handoff' then array['post_id'] when 'experience' then array[]::text[] else null end;
 if allowed is null or jsonb_typeof(args) is distinct from 'object' or args-allowed<>'{}' then
  raise exception using errcode='PT422',message='invalid_social_input'; end if;
 if action='experience' then
  select count(*)::integer into done from public.core_handoff_receipts r where r.member_id=actor and core_private.handoff_complete(r.post_id);
  return jsonb_build_object('score',core_private.reputation_score(actor),'level',core_private.reputation_level(actor),
   'completed_orders',done,'completed_post_ids',(select coalesce(jsonb_agg(r.post_id),'[]'::jsonb) from public.core_handoff_receipts r where r.member_id=actor and core_private.handoff_complete(r.post_id)),'history_count',(select count(*) from public.core_posts pp where (pp.author_id=actor or exists(select 1 from public.core_join_requests j where j.post_id=pp.id and j.requester_id=actor)) and (pp.cancelled or pp.deadline_at<=clock_timestamp())));
 end if;
 if action in ('order','handoff') then
  target:=(args->>'post_id')::uuid;
  -- Reuse the existing visibility/session boundary before exposing membership.
  value:=public.core_dispatch('post',jsonb_build_object('post_id',target));
  select * into p from public.core_posts where id=target for update;
  is_member:=p.author_id=actor or exists(select 1 from public.core_join_requests where post_id=target and requester_id=actor and status='accepted');
  if action='handoff' then
   if not is_member then raise exception using errcode='PT403',message='chat_members_only'; end if;
   if p.cancelled or p.demo_batch is not null or not exists(select 1 from public.core_join_requests where post_id=target and status='accepted') then
    raise exception using errcode='PT409',message='handoff_not_ready'; end if;
   insert into public.core_handoff_receipts(post_id,member_id) values(target,actor) on conflict do nothing;
  end if;
  select coalesce(jsonb_agg(m.info order by m.host desc,m.id),'[]') into members from (
   select p.author_id id,true host,jsonb_build_object('id',p.author_id,'name',pr.display_name,'badge',pr.badge,'level',core_private.reputation_level(pr.id),'host',true) info from public.core_profiles pr where pr.id=p.author_id
   union all select j.requester_id,false,jsonb_build_object('id',j.requester_id,'name',pr.display_name,'badge',pr.badge,'level',core_private.reputation_level(pr.id),'host',false)
    from public.core_join_requests j join public.core_profiles pr on pr.id=j.requester_id where j.post_id=target and j.status='accepted' and (is_member or j.requester_id=actor)
  ) m;
  return jsonb_build_object('post',value,'members',members,'formed',(value->>'joined')::integer>1,
   'completed',coalesce(core_private.handoff_complete(target),false),
   'confirmed_count',(select count(*) from public.core_handoff_receipts where post_id=target),
   'my_confirmed',exists(select 1 from public.core_handoff_receipts where post_id=target and member_id=actor),
   'can_confirm',is_member and not p.cancelled and p.demo_batch is null and (value->>'joined')::integer>1);
 end if;
 if action='list' then
  k:=coalesce(args->>'kind','all'); page_size:=coalesce((args->>'limit')::integer,30);
  if k not in ('all','primary','group','direct') or page_size not between 1 and 50 or (args ? 'deleted' and jsonb_typeof(args->'deleted')<>'boolean') then
   raise exception using errcode='PT422',message='invalid_social_input'; end if;
  if coalesce(args->>'cursor','')<>'' then
   c:=(args->>'cursor')::jsonb;
   if c->>'owner' is distinct from actor::text or c->>'kind' is distinct from k or c->>'deleted' is distinct from coalesce(args->>'deleted','false') then
    raise exception using errcode='PT422',message='invalid_social_cursor'; end if;
  end if;
  with candidates as (
   select 'group' kind,pp.id from public.core_posts pp where k in ('all','primary','group') and (pp.author_id=actor or exists(select 1 from public.core_join_requests j where j.post_id=pp.id and j.requester_id=actor and j.status='accepted'))
   union all select 'direct',dd.id from public.core_direct_threads dd where k in ('all','primary','direct') and actor in (dd.host_id,dd.guest_id)
   union all select 'stealth',sp.id from public.core_stealth_proposals sp where k in ('all','primary','group') and actor=any(sp.owners)
  ), visible as (
   select core_private.social_thread(kind,id,actor) item from candidates
  ), all_items as (
   select item from visible where item is not null and (k<>'primary' or item->>'inbox'='primary') and (item->>'deleted')::boolean=coalesce((args->>'deleted')::boolean,false)
  ), page as (
   select item from all_items where c is null or ((item->>'pinned')::boolean,(item->>'updated_at')::timestamptz,item->>'id',item->>'kind')<((c->>'pinned')::boolean,(c->>'at')::timestamptz,c->>'id',c->>'thread_kind')
   order by (item->>'pinned')::boolean desc,(item->>'updated_at')::timestamptz desc,item->>'id' desc,item->>'kind' desc limit page_size+1
  ) select coalesce(jsonb_agg(item order by (item->>'pinned')::boolean desc,(item->>'updated_at')::timestamptz desc,item->>'id' desc,item->>'kind' desc),'[]'),(select coalesce(sum((item->>'unread_count')::integer),0) from all_items) into items,unread from page;
  value:=null;
  if jsonb_array_length(items)>page_size then
   items:=items-page_size; result:=items->(page_size-1);
   value:=to_jsonb(jsonb_build_object('owner',actor,'kind',k,'deleted',coalesce(args->>'deleted','false'),'pinned',result->'pinned','at',result->'updated_at','id',result->>'id','thread_kind',result->>'kind')::text);
  end if;
  return jsonb_build_object('items',items,'next_cursor',value,'total_unread',unread);
 end if;
 if action='start' then
  target:=(args->>'post_id')::uuid;
  value:=public.core_dispatch('post',jsonb_build_object('post_id',target));
  select * into p from public.core_posts where id=target for key share;
  if p.id is null or p.author_id=actor or core_private.users_blocked(actor,p.author_id) then
   raise exception using errcode='PT403',message='direct_message_unavailable'; end if;
  if (select count(*) from public.core_direct_threads where guest_id=actor and created_at>clock_timestamp()-interval '1 minute')>=10 then
   raise exception using errcode='PT429',message='direct_message_rate_limit'; end if;
  insert into public.core_direct_threads(post_id,host_id,guest_id) values(target,p.author_id,actor) on conflict(post_id,guest_id) do update set post_id=excluded.post_id returning * into d;
  insert into public.core_thread_preferences(owner_id,kind,thread_id) values(actor,'direct',d.id) on conflict do nothing;
  update public.core_thread_preferences set hidden_at=null where owner_id=actor and kind='direct' and thread_id=d.id;
  return core_private.social_thread('direct',d.id,actor);
 end if;
 target:=(args->>'thread_id')::uuid; k:=case when action='preference' then args->>'kind' else 'direct' end;
 if k is null or k not in ('group','direct','stealth') or target is null then raise exception using errcode='PT422',message='invalid_social_input'; end if;
 value:=core_private.social_thread(k,target,actor);
 if value is null then raise exception using errcode='PT404',message='conversation_not_found'; end if;
 if action='preference' then
  if (args ? 'inbox' and args->>'inbox' not in ('primary','general')) or exists(select 1 from jsonb_each(args) x where x.key in ('pinned','deleted','muted') and jsonb_typeof(x.value)<>'boolean') then
   raise exception using errcode='PT422',message='invalid_social_input'; end if;
  insert into public.core_thread_preferences(owner_id,kind,thread_id,pinned,inbox,muted) values(actor,k,target,(value->>'pinned')::boolean,value->>'inbox',(value->>'muted')::boolean) on conflict do nothing;
  update public.core_thread_preferences set pinned=coalesce((args->>'pinned')::boolean,pinned),inbox=coalesce(args->>'inbox',inbox),muted=coalesce((args->>'muted')::boolean,muted),
   hidden_at=case when args ? 'deleted' then case when (args->>'deleted')::boolean then clock_timestamp() else null end else hidden_at end
   where owner_id=actor and kind=k and thread_id=target;
  return core_private.social_thread(k,target,actor);
 end if;
 select * into d from public.core_direct_threads where id=target for update;
 if action='send' then
  text_value:=btrim(args->>'content'); key_value:=args->>'client_key';
  if jsonb_typeof(args->'content') is distinct from 'string' or char_length(text_value) not between 1 and 2000 or char_length(key_value) not between 12 and 100 or key_value is null then
   raise exception using errcode='PT422',message='invalid_chat_input'; end if;
  select * into last_msg from public.core_direct_messages where sender_id=actor and client_key=key_value;
  if found then
   if last_msg.thread_id<>target or last_msg.content<>text_value then raise exception using errcode='PT409',message='message_key_payload_conflict'; end if;
   return core_private.social_message(last_msg);
  end if;
  if (select count(*) from public.core_direct_messages where sender_id=actor and created_at>clock_timestamp()-interval '1 minute')>=30 then
   raise exception using errcode='PT429',message='direct_message_rate_limit'; end if;
  insert into public.core_direct_messages(thread_id,sender_id,content,client_key) values(target,actor,text_value,key_value) returning * into last_msg;
  return core_private.social_message(last_msg);
 elsif action='read' then
  select * into last_msg from public.core_direct_messages where id=(args->>'message_id')::uuid and thread_id=target;
  if not found then raise exception using errcode='PT404',message='message_not_found'; end if;
  insert into public.core_thread_preferences(owner_id,kind,thread_id,read_at) values(actor,'direct',target,last_msg.created_at) on conflict(owner_id,kind,thread_id)
   do update set read_at=greatest(core_thread_preferences.read_at,excluded.read_at);
  return jsonb_build_object('ok',true);
 end if;
 page_size:=coalesce((args->>'limit')::integer,30);
 if page_size not between 1 and 50 then raise exception using errcode='PT422',message='invalid_social_input'; end if;
 if coalesce(args->>'cursor','')<>'' then
  c:=(args->>'cursor')::jsonb;
  if c->>'owner' is distinct from actor::text or c->>'thread' is distinct from target::text then raise exception using errcode='PT422',message='invalid_social_cursor'; end if;
 end if;
 select coalesce(jsonb_agg(core_private.social_message(m) order by m.created_at desc,m.id desc),'[]') into items from (
  select * from public.core_direct_messages m where thread_id=target and (c is null or (m.created_at,m.id)<((c->>'at')::timestamptz,(c->>'id')::uuid)) order by created_at desc,id desc limit page_size+1
 ) m;
 result:=null;
 if jsonb_array_length(items)>page_size then
  items:=items-page_size; result:=to_jsonb(jsonb_build_object('owner',actor,'thread',target,'at',items->(page_size-1)->>'created_at','id',items->(page_size-1)->>'id')::text);
 end if;
 return jsonb_build_object('thread',value,'messages',items,'next_cursor',result,'can_send',true);
exception when invalid_text_representation or numeric_value_out_of_range or invalid_parameter_value or datetime_field_overflow then
 raise exception using errcode='PT422',message='invalid_social_input';
end $function$;
-- Include the new private bucket in the existing resumable account cleanup.
alter function core_private.deletion_counts(uuid) rename to deletion_counts_before_personal_images;
revoke all on function core_private.deletion_counts_before_personal_images(uuid) from public,anon,authenticated;
create function core_private.deletion_counts(actor uuid) returns jsonb
language sql stable security definer set search_path='' as $$
 select jsonb_set(v,'{images}',to_jsonb((v->>'images')::integer+(select count(*)::integer from public.core_personal_images where owner_id=actor)))
 from (select core_private.deletion_counts_before_personal_images(actor) v) x
$$;
revoke all on function core_private.deletion_counts(uuid) from public,anon,authenticated;
alter function core_private.account_deletion(text,jsonb) rename to account_deletion_before_personal_images;
revoke all on function core_private.account_deletion_before_personal_images(text,jsonb) from public,anon,authenticated;
create function core_private.account_deletion(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=core_private.account_guard(true); value jsonb; images jsonb;
begin
 if action='finish' and core_private.account_pending(actor) then
  if exists(select 1 from storage.objects o join public.core_personal_images a on o.name=a.id::text||'.jpg'
   where o.bucket_id='core-personal-images' and a.owner_id=actor) then
   raise exception using errcode='PT409',message='account_deletion_storage_remaining'; end if;
  delete from public.core_personal_images where owner_id=actor;
 end if;
 value:=core_private.account_deletion_before_personal_images(action,args);
 if action='assets' then
  select coalesce(jsonb_agg(jsonb_build_object('path',id::text||'.jpg','bucket','core-personal-images')),'[]') into images
   from public.core_personal_images where owner_id=actor;
  value:=jsonb_set(value,'{items}',(value->'items')||images);
 end if;
 return value;
end $$;
revoke all on function core_private.account_deletion(text,jsonb) from public,anon,authenticated;
grant execute on function core_private.account_deletion(text,jsonb) to authenticated;
