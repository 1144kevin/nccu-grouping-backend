-- Private, bounded image storage. Existing text post writes remain unchanged.
create table public.core_media_assets (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references public.core_profiles(id),
  request_key text not null check(char_length(request_key) between 12 and 100),
  sha256 text not null check(sha256 ~ '^[0-9a-f]{64}$'),
  width integer not null check(width between 1 and 1600),
  height integer not null check(height between 1 and 1600),
  state text not null default 'pending' check(state in ('pending','ready')),
  verified_object_id uuid,
  created_at timestamptz not null default clock_timestamp(),
  unique(owner_id,request_key)
);
create table public.core_post_images (
  post_id uuid primary key references public.core_posts(id) on delete cascade,
  asset_id uuid not null unique references public.core_media_assets(id)
);
alter table public.core_media_assets enable row level security;
alter table public.core_post_images enable row level security;
revoke all on public.core_media_assets,public.core_post_images from public,anon,authenticated;
create policy core_media_assets_rpc_only on public.core_media_assets for all to anon,authenticated using(false) with check(false);
create policy core_post_images_rpc_only on public.core_post_images for all to anon,authenticated using(false) with check(false);

-- A narrowly scoped server attestation secret, separate from all Supabase admin/API keys.
create table core_private.media_signing_keys(version integer primary key,secret bytea not null check(octet_length(secret)=32));
alter table core_private.media_signing_keys enable row level security;
revoke all on core_private.media_signing_keys from public,anon,authenticated;
create policy media_signing_keys_no_direct_access on core_private.media_signing_keys for all to anon,authenticated using(false) with check(false);

insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
  values('core-post-images','core-post-images',false,2097152,array['image/jpeg']);

create function core_private.media_actor() returns uuid
language sql stable security definer set search_path = '' as $$
  select u.id from auth.users u where u.id=auth.uid() and not coalesce(u.is_anonymous,false)
    and exists(select 1 from auth.sessions s where s.user_id=u.id
      and s.id::text=auth.jwt()->>'session_id' and (s.not_after is null or s.not_after>clock_timestamp()));
$$;
revoke all on function core_private.media_actor() from public,anon,authenticated;

create function core_private.media_access(operation text,object_name text) returns boolean
language plpgsql security definer set search_path = '' as $$
declare actor uuid:=core_private.media_actor(); asset public.core_media_assets;
begin
  if actor is null then return false; end if;
  if operation in ('insert','delete') then
    -- Serialize object mutations with verification, publication and reservation removal.
    select * into asset from public.core_media_assets a
      where a.owner_id=actor and object_name=a.owner_id::text||'/'||a.id::text||'.jpg' for update;
  else
    select * into asset from public.core_media_assets a
      where object_name=a.owner_id::text||'/'||a.id::text||'.jpg';
  end if;
  if not found then return false; end if;
  return case operation
    when 'insert' then asset.owner_id=actor and asset.state='pending'
    when 'select' then asset.owner_id=actor or (asset.state='ready' and exists(
      select 1 from public.core_post_images pi join public.core_posts p on p.id=pi.post_id where pi.asset_id=asset.id))
    when 'delete' then asset.owner_id=actor and not exists(select 1 from public.core_post_images pi where pi.asset_id=asset.id)
    else false end;
end;
$$;
revoke all on function core_private.media_access(text,text) from public,anon,authenticated;
grant execute on function core_private.media_access(text,text) to authenticated;
create policy core_images_insert on storage.objects for insert to authenticated
  with check(bucket_id='core-post-images' and owner_id=auth.uid()::text and core_private.media_access('insert',name));
create policy core_images_select on storage.objects for select to authenticated
  using(bucket_id='core-post-images' and core_private.media_access('select',name));
create policy core_images_delete on storage.objects for delete to authenticated
  using(bucket_id='core-post-images' and core_private.media_access('delete',name));
-- No UPDATE/upsert permission: published bytes cannot be replaced under an existing ID.

create or replace function core_private.post_view(target_id uuid,actor uuid) returns jsonb
language sql stable set search_path = '' as $$
  select post.payload || jsonb_build_object(
    'id',post.id,'author_id',post.author_id,'author_name',p.display_name,'author_badge',p.badge,
    'is_demo',post.demo_batch is not null,
    'image_id',(select pi.asset_id from public.core_post_images pi where pi.post_id=post.id),
    'created_at',post.created_at,'version',post.version,'joined',1+counts.accepted,
    'status',case when post.cancelled then 'cancelled' when post.deadline_at<=clock_timestamp() then 'expired'
      when 1+counts.accepted>=post.capacity then 'full' else 'open' end,
    'my_status',case when actor=post.author_id then 'host' else coalesce(j.status,'none') end,
    'can_edit',actor=post.author_id and not post.cancelled and post.deadline_at>clock_timestamp() and counts.active=0)
  from public.core_posts post join public.core_profiles p on p.id=post.author_id
  cross join lateral(select count(*) filter(where status='accepted') as accepted,
    count(*) filter(where status in ('pending','accepted')) as active from public.core_join_requests where post_id=post.id) counts
  left join public.core_join_requests j on j.post_id=post.id and j.requester_id=actor where post.id=target_id;
$$;

create function core_private.media(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '8s' as $$
declare actor uuid:=core_private.media_actor(); asset public.core_media_assets; result jsonb;
  allowed text[]; linked uuid; object_path text; metadata jsonb; saved_image uuid; storage_id uuid;
  expected_proof text; signing_secret bytea;
begin
  if actor is null then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  allowed:=case action when 'reserve' then array['request_key','sha256','width','height']
    when 'ready' then array['image_id','storage_id','proof'] when 'snapshot' then array['image_id'] when 'read' then array['image_id']
    when 'delete_prepare' then array['image_id'] when 'delete_finish' then array['image_id']
    when 'publish' then array['image_id','value','request_key'] when 'list' then array[]::text[] else null end;
  if allowed is null or jsonb_typeof(args) is distinct from 'object' or args-allowed<>'{}'::jsonb then
    raise exception using errcode='PT422',message='invalid_image_input'; end if;
  if action='list' then
    select coalesce(jsonb_agg(jsonb_build_object('id',a.id,'width',a.width,'height',a.height,
      'state',a.state,'created_at',a.created_at) order by a.created_at desc),'[]') into result
      from public.core_media_assets a where a.owner_id=actor
      and not exists(select 1 from public.core_post_images pi where pi.asset_id=a.id);
    return jsonb_build_object('items',result);
  end if;
  if action='reserve' then
    if jsonb_typeof(args->'request_key') is distinct from 'string' or char_length(args->>'request_key') not between 12 and 100
      or jsonb_typeof(args->'sha256') is distinct from 'string' or (args->>'sha256') !~ '^[0-9a-f]{64}$'
      or jsonb_typeof(args->'width') is distinct from 'number' or (args->>'width') !~ '^[0-9]+$'
      or jsonb_typeof(args->'height') is distinct from 'number' or (args->>'height') !~ '^[0-9]+$'
      or (args->>'width')::integer not between 1 and 1600 or (args->>'height')::integer not between 1 and 1600 then
      raise exception using errcode='PT422',message='invalid_image_input'; end if;
    perform pg_advisory_xact_lock(hashtextextended('core-media-reservations',0));
    select * into asset from public.core_media_assets where owner_id=actor and request_key=args->>'request_key';
    if found then
      if asset.sha256<>args->>'sha256' or asset.width<>(args->>'width')::integer or asset.height<>(args->>'height')::integer then
        raise exception using errcode='PT409',message='image_key_conflict'; end if;
    else
      -- A reservation is retained until its Storage object is gone. This bounds stored bytes even if clients bypass FastAPI.
      if (select count(*) from public.core_media_assets)>=100 or
        (select count(*) from public.core_media_assets where owner_id=actor)>=20 then
        raise exception using errcode='PT409',message='image_storage_limit'; end if;
      insert into public.core_media_assets(owner_id,request_key,sha256,width,height)
        values(actor,args->>'request_key',args->>'sha256',(args->>'width')::integer,(args->>'height')::integer) returning * into asset;
    end if;
  else
    select * into asset from public.core_media_assets where id=(args->>'image_id')::uuid for update;
    if not found then raise exception using errcode='PT404',message='image_not_found'; end if;
    if action='read' then
      if not core_private.media_access('select',asset.owner_id::text||'/'||asset.id::text||'.jpg') then
        raise exception using errcode='PT404',message='image_not_found'; end if;
    elsif asset.owner_id<>actor then raise exception using errcode='PT404',message='image_not_found'; end if;
  end if;
  object_path:=asset.owner_id::text||'/'||asset.id::text||'.jpg';
  if action in ('snapshot','ready','publish','read') then
    select o.metadata,o.id into metadata,storage_id from storage.objects o where o.bucket_id='core-post-images'
      and o.name=object_path and o.owner_id=asset.owner_id::text and not coalesce(o.is_delete_marker,false);
    if not found or metadata->>'mimetype' is distinct from 'image/jpeg'
      or coalesce((metadata->>'size')::bigint,0) not between 1 and 2097152 then
      raise exception using errcode='PT409',message='image_upload_incomplete'; end if;
  end if;
  if action='ready' then
    select secret into signing_secret from core_private.media_signing_keys where version=1;
    expected_proof:=encode(extensions.hmac(convert_to(asset.id::text||':'||actor::text||':'||asset.sha256||':'||storage_id::text,'UTF8'),signing_secret,'sha256'),'hex');
    if signing_secret is null or args->>'storage_id' is distinct from storage_id::text
      or jsonb_typeof(args->'proof') is distinct from 'string' or args->>'proof' is distinct from expected_proof then
      raise exception using errcode='PT403',message='image_verification_required'; end if;
    update public.core_media_assets set state='ready',verified_object_id=storage_id where id=asset.id;
    asset.state:='ready';
  elsif action='publish' then
    if asset.state<>'ready' or asset.verified_object_id is distinct from storage_id then
      raise exception using errcode='PT409',message='image_upload_incomplete'; end if;
    result:=public.core_dispatch('create',jsonb_build_object('value',args->'value','request_key',args->>'request_key'));
    linked:=(result->>'id')::uuid;
    select pi.asset_id into saved_image from public.core_post_images pi where pi.post_id=linked;
    if found and saved_image<>asset.id then raise exception using errcode='PT409',message='image_key_conflict'; end if;
    if exists(select 1 from public.core_post_images pi where pi.asset_id=asset.id and pi.post_id<>linked) then
      raise exception using errcode='PT409',message='image_already_published'; end if;
    if saved_image is null and not coalesce((result->>'can_edit')::boolean,false) then
      raise exception using errcode='PT409',message='post_has_active_requests'; end if;
    insert into public.core_post_images(post_id,asset_id) values(linked,asset.id) on conflict(post_id) do nothing;
    return core_private.post_view(linked,actor);
  elsif action='read' then
    if asset.state<>'ready' or asset.verified_object_id is distinct from storage_id then
      raise exception using errcode='PT409',message='image_upload_incomplete'; end if;
  elsif action in ('delete_prepare','delete_finish') then
    if exists(select 1 from public.core_post_images where asset_id=asset.id) then
      raise exception using errcode='PT409',message='image_already_published'; end if;
    if action='delete_finish' then
      if exists(select 1 from storage.objects where bucket_id='core-post-images' and name=object_path) then
        raise exception using errcode='PT409',message='image_delete_incomplete'; end if;
      delete from public.core_media_assets where id=asset.id;
      return jsonb_build_object('deleted',true);
    end if;
  end if;
  return jsonb_build_object('id',asset.id,'object_path',object_path,'width',asset.width,'height',asset.height,'state',asset.state,
    'storage_id',case when action='snapshot' then storage_id else null end);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range then
  raise exception using errcode='PT422',message='invalid_image_input';
end;
$$;
revoke all on function core_private.media(text,jsonb) from public,anon,authenticated;
grant execute on function core_private.media(text,jsonb) to authenticated;
create function public.core_media(action text,args jsonb default '{}') returns jsonb
language sql security invoker set search_path = '' as $$ select core_private.media(action,args); $$;
revoke all on function public.core_media(text,jsonb) from public,anon,authenticated;
grant execute on function public.core_media(text,jsonb) to authenticated;
