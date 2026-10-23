-- Private chat photos use opaque paths so anonymous coordination never exposes account IDs.
create table public.core_chat_images (like public.core_media_assets including all);
alter table public.core_chat_images add foreign key(owner_id) references public.core_profiles(id) on delete cascade;
create table public.core_chat_photos (
 asset_id uuid primary key references public.core_chat_images(id) on delete cascade,
 owner_id uuid not null references public.core_profiles(id) on delete cascade,
 kind text not null check(kind in ('group','direct','stealth')), thread_id uuid not null,
 message_id uuid not null unique, request_key uuid not null, unique(owner_id,request_key)
);
create index core_chat_photos_thread on public.core_chat_photos(kind,thread_id);
alter table public.core_chat_images enable row level security;
alter table public.core_chat_photos enable row level security;
revoke all on public.core_chat_images,public.core_chat_photos from public,anon,authenticated;
create policy chat_images_rpc_only on public.core_chat_images for all to authenticated using(false) with check(false);
create policy chat_photos_rpc_only on public.core_chat_photos for all to authenticated using(false) with check(false);
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
 values('core-chat-images','core-chat-images',false,2097152,array['image/jpeg']);

create function core_private.chat_photo_access(photo public.core_chat_photos,actor uuid) returns boolean
language plpgsql security definer set search_path='' as $$
declare value jsonb;
begin
 if actor is distinct from core_private.media_actor() or core_private.account_pending(actor)
  or core_private.users_blocked(actor,photo.owner_id) then return false; end if;
 if photo.kind='group' then
  if not exists(select 1 from public.core_chat_messages where id=photo.message_id and post_id=photo.thread_id and not moderation_hidden) then return false; end if;
  return core_private.social_thread('group',photo.thread_id,actor) is not null;
 elsif photo.kind='direct' then
  return exists(select 1 from public.core_direct_messages where id=photo.message_id and thread_id=photo.thread_id)
   and core_private.social_thread('direct',photo.thread_id,actor) is not null;
 end if;
 value:=public.core_stealth_quick('state',jsonb_build_object('proposal_id',photo.thread_id));
 return (value->>'opened')::boolean and exists(select 1 from public.core_stealth_room_messages where id=photo.message_id and proposal_id=photo.thread_id);
exception when sqlstate 'PT404' or sqlstate 'PT403' or sqlstate 'PT401' then return false;
end $$;
revoke all on function core_private.chat_photo_access(public.core_chat_photos,uuid) from public,anon,authenticated;

create function core_private.chat_media_access(operation text,object_name text) returns boolean
language plpgsql security definer set search_path='' as $$
declare actor uuid:=core_private.media_actor(); asset public.core_chat_images; photo public.core_chat_photos;
begin
 if actor is null then return false; end if;
 perform pg_advisory_xact_lock(hashtextextended('core-account-safety-v1',0));
 perform 1 from auth.users where id=actor for key share;
 select * into asset from public.core_chat_images where object_name=id::text||'.jpg' for update;
 if not found then return false; end if;
 select * into photo from public.core_chat_photos where asset_id=asset.id;
 if core_private.account_pending(actor) then return operation='delete' and asset.owner_id=actor; end if;
 return case operation
  when 'insert' then asset.owner_id=actor and asset.state='pending'
  when 'select' then case when photo.asset_id is null then asset.owner_id=actor else core_private.chat_photo_access(photo,actor) end
  when 'delete' then asset.owner_id=actor and photo.asset_id is null
  else false end;
end $$;
revoke all on function core_private.chat_media_access(text,text) from public,anon,authenticated;
grant execute on function core_private.chat_media_access(text,text) to authenticated;
create policy chat_images_insert on storage.objects for insert to authenticated with check
 (bucket_id='core-chat-images' and owner_id=auth.uid()::text and core_private.chat_media_access('insert',name));
create policy chat_images_select on storage.objects for select to authenticated using
 (bucket_id='core-chat-images' and core_private.chat_media_access('select',name));
create policy chat_images_delete on storage.objects for delete to authenticated using
 (bucket_id='core-chat-images' and core_private.chat_media_access('delete',name));

create function core_private.chat_photo(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=core_private.account_guard(); asset public.core_chat_images; photo public.core_chat_photos;
 k text; target uuid; value jsonb; object_id uuid;
begin
 if action not in ('send','read') or jsonb_typeof(args) is distinct from 'object'
  or args-(case when action='send' then array['kind','thread_id','image_id','request_key'] else array['image_id'] end)<>'{}'
 then raise exception using errcode='PT422',message='invalid_image_input'; end if;
 select * into asset from public.core_chat_images where id=(args->>'image_id')::uuid for update;
 if not found then raise exception using errcode='PT404',message='image_not_found'; end if;
 if action='read' then
  select * into photo from public.core_chat_photos where asset_id=asset.id;
  if not found or not core_private.chat_photo_access(photo,actor) then raise exception using errcode='PT404',message='image_not_found'; end if;
  return public.core_chat_media('read',jsonb_build_object('image_id',asset.id));
 end if;
 k:=args->>'kind'; target:=(args->>'thread_id')::uuid;
 if k is null or k not in ('group','direct','stealth') or target is null or args->>'request_key' is null then
  raise exception using errcode='PT422',message='invalid_image_input'; end if;
 if asset.owner_id<>actor then raise exception using errcode='PT404',message='image_not_found'; end if;
 select * into photo from public.core_chat_photos where owner_id=actor and request_key=(args->>'request_key')::uuid;
 if found then
  if photo.asset_id<>asset.id or photo.kind<>k or photo.thread_id<>target then raise exception using errcode='PT409',message='image_key_conflict'; end if;
  if not core_private.chat_photo_access(photo,actor) then raise exception using errcode='PT404',message='image_not_found'; end if;
  return jsonb_build_object('id',photo.message_id,'image_id',asset.id);
 end if;
 if exists(select 1 from public.core_chat_photos where asset_id=asset.id) then raise exception using errcode='PT409',message='image_already_published'; end if;
 select id into object_id from storage.objects where bucket_id='core-chat-images' and name=asset.id::text||'.jpg' and owner_id=actor::text and not coalesce(is_delete_marker,false);
 if asset.state<>'ready' or object_id is null or asset.verified_object_id is distinct from object_id then raise exception using errcode='PT409',message='image_upload_incomplete'; end if;
 -- The existing send RPCs enforce live membership, cancellation, blocks and rate limits.
 if k='group' then
  value:=public.core_chat('send',jsonb_build_object('post_id',target,'content','📷 照片','client_key',args->>'request_key'));
 elsif k='direct' then
  value:=public.core_social('send',jsonb_build_object('thread_id',target,'content','📷 照片','client_key',args->>'request_key'));
 else
  value:=public.core_stealth_quick('send',jsonb_build_object('proposal_id',target,'content','📷 照片','request_key',args->>'request_key'));
 end if;
 insert into public.core_chat_photos(asset_id,owner_id,kind,thread_id,message_id,request_key)
  values(asset.id,actor,k,target,(value->>'id')::uuid,(args->>'request_key')::uuid);
 return jsonb_build_object('id',value->>'id','image_id',asset.id);
exception when invalid_text_representation or not_null_violation then raise exception using errcode='PT422',message='invalid_image_input';
end $$;
revoke all on function core_private.chat_photo(text,jsonb) from public,anon,authenticated;
grant execute on function core_private.chat_photo(text,jsonb) to authenticated;
create function public.core_chat_photo(action text,args jsonb default '{}') returns jsonb
language sql security invoker set search_path='' as $$ select core_private.chat_photo(action,args) $$;
revoke all on function public.core_chat_photo(text,jsonb) from public,anon,authenticated;
grant execute on function public.core_chat_photo(text,jsonb) to authenticated;

alter function core_private.chat_message_view(public.core_chat_messages) rename to chat_message_view_before_photos;
revoke all on function core_private.chat_message_view_before_photos(public.core_chat_messages) from public,anon,authenticated;
create function core_private.chat_message_view(message public.core_chat_messages) returns jsonb
language sql stable set search_path='' as $$
 select core_private.chat_message_view_before_photos(message)||jsonb_build_object('image_id',case when not message.moderation_hidden then (select asset_id from public.core_chat_photos where message_id=message.id) end)
$$;
revoke all on function core_private.chat_message_view(public.core_chat_messages) from public,anon,authenticated;
alter function core_private.social_message(public.core_direct_messages) rename to social_message_before_photos;
revoke all on function core_private.social_message_before_photos(public.core_direct_messages) from public,anon,authenticated;
create function core_private.social_message(m public.core_direct_messages) returns jsonb
language sql stable set search_path='' as $$
 select core_private.social_message_before_photos(m)||jsonb_build_object('image_id',(select asset_id from public.core_chat_photos where message_id=m.id))
$$;
revoke all on function core_private.social_message(public.core_direct_messages) from public,anon,authenticated;
alter function core_private.stealth_quick(text,jsonb) rename to stealth_quick_before_photos;
revoke all on function core_private.stealth_quick_before_photos(text,jsonb) from public,anon,authenticated;
create function core_private.stealth_quick(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare value jsonb;
begin
 value:=core_private.stealth_quick_before_photos(action,args);
 if action='state' and value ? 'items' then
  return jsonb_set(value,'{items}',(select coalesce(jsonb_agg(m||jsonb_build_object('image_id',(select asset_id from public.core_chat_photos where message_id=(m->>'id')::uuid)) order by m->>'created_at',m->>'id'),'[]') from jsonb_array_elements(value->'items') m));
 end if;
 return value;
end $$;
revoke all on function core_private.stealth_quick(text,jsonb) from public,anon,authenticated;
grant execute on function core_private.stealth_quick(text,jsonb) to authenticated;

create function core_private.chat_media(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '8s' as $$
declare actor uuid:=core_private.account_guard(); asset public.core_chat_images; result jsonb;
  allowed text[]; linked uuid; object_path text; metadata jsonb; saved_image uuid; storage_id uuid;
  expected_proof text; signing_secret bytea;
begin
  if actor is null then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  allowed:=case action when 'reserve' then array['request_key','sha256','width','height']
    when 'ready' then array['image_id','storage_id','proof'] when 'snapshot' then array['image_id'] when 'read' then array['image_id']
    when 'delete_prepare' then array['image_id'] when 'delete_finish' then array['image_id']
     when 'list' then array[]::text[] else null end;
  if allowed is null or jsonb_typeof(args) is distinct from 'object' or args-allowed<>'{}'::jsonb then
    raise exception using errcode='PT422',message='invalid_image_input'; end if;
  if action='list' then
    select coalesce(jsonb_agg(jsonb_build_object('id',a.id,'width',a.width,'height',a.height,
      'state',a.state,'created_at',a.created_at) order by a.created_at desc),'[]') into result
      from public.core_chat_images a where a.owner_id=actor
      and not exists(select 1 from public.core_chat_photos pi where pi.asset_id=a.id);
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
    select * into asset from public.core_chat_images where owner_id=actor and request_key=args->>'request_key';
    if found then
      if asset.sha256<>args->>'sha256' or asset.width<>(args->>'width')::integer or asset.height<>(args->>'height')::integer then
        raise exception using errcode='PT409',message='image_key_conflict'; end if;
    else
      -- A reservation is retained until its Storage object is gone. This bounds stored bytes even if clients bypass FastAPI.
      if (select count(*) from public.core_chat_images)>=100 or
        (select count(*) from public.core_chat_images where owner_id=actor)>=20 then
        raise exception using errcode='PT409',message='image_storage_limit'; end if;
      insert into public.core_chat_images(owner_id,request_key,sha256,width,height)
        values(actor,args->>'request_key',args->>'sha256',(args->>'width')::integer,(args->>'height')::integer) returning * into asset;
    end if;
  else
    select * into asset from public.core_chat_images where id=(args->>'image_id')::uuid for update;
    if not found then raise exception using errcode='PT404',message='image_not_found'; end if;
    if action='read' then
      if not core_private.chat_media_access('select',asset.id::text||'.jpg') then
        raise exception using errcode='PT404',message='image_not_found'; end if;
    elsif asset.owner_id<>actor then raise exception using errcode='PT404',message='image_not_found'; end if;
  end if;
  object_path:=asset.id::text||'.jpg';
  if action in ('snapshot','ready','publish','read') then
    select o.metadata,o.id into metadata,storage_id from storage.objects o where o.bucket_id='core-chat-images'
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
    update public.core_chat_images set state='ready',verified_object_id=storage_id where id=asset.id;
    asset.state:='ready';
  elsif action='read' then
    if asset.state<>'ready' or asset.verified_object_id is distinct from storage_id then
      raise exception using errcode='PT409',message='image_upload_incomplete'; end if;
  elsif action in ('delete_prepare','delete_finish') then
    if exists(select 1 from public.core_chat_photos where asset_id=asset.id) then
      raise exception using errcode='PT409',message='image_already_published'; end if;
    if action='delete_finish' then
      if exists(select 1 from storage.objects where bucket_id='core-chat-images' and name=object_path) then
        raise exception using errcode='PT409',message='image_delete_incomplete'; end if;
      delete from public.core_chat_images where id=asset.id;
      return jsonb_build_object('deleted',true);
    end if;
  end if;
  return jsonb_build_object('id',asset.id,'object_path',object_path,'width',asset.width,'height',asset.height,'state',asset.state,
    'storage_id',case when action='snapshot' then storage_id else null end);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range then
  raise exception using errcode='PT422',message='invalid_image_input';
end;
$$;
revoke all on function core_private.chat_media(text,jsonb) from public,anon,authenticated;
grant execute on function core_private.chat_media(text,jsonb) to authenticated;
create function public.core_chat_media(action text,args jsonb default '{}') returns jsonb
language sql security invoker set search_path = '' as $$ select core_private.chat_media(action,args); $$;
revoke all on function public.core_chat_media(text,jsonb) from public,anon,authenticated;
grant execute on function public.core_chat_media(text,jsonb) to authenticated;

-- Include the new private bucket in the existing resumable account cleanup.
alter function core_private.deletion_counts(uuid) rename to deletion_counts_before_chat_photos;
revoke all on function core_private.deletion_counts_before_chat_photos(uuid) from public,anon,authenticated;
create function core_private.deletion_counts(actor uuid) returns jsonb
language sql stable security definer set search_path='' as $$
 select jsonb_set(v,'{images}',to_jsonb((v->>'images')::integer+(select count(*)::integer from public.core_chat_images where owner_id=actor)))
 from (select core_private.deletion_counts_before_chat_photos(actor) v) x
$$;
revoke all on function core_private.deletion_counts(uuid) from public,anon,authenticated;
alter function core_private.account_deletion(text,jsonb) rename to account_deletion_before_chat_photos;
revoke all on function core_private.account_deletion_before_chat_photos(text,jsonb) from public,anon,authenticated;
create function core_private.account_deletion(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=core_private.account_guard(true); value jsonb; images jsonb;
begin
 if action='finish' and core_private.account_pending(actor) then
  if exists(select 1 from storage.objects o join public.core_chat_images a on o.name=a.id::text||'.jpg'
   where o.bucket_id='core-chat-images' and a.owner_id=actor) then
   raise exception using errcode='PT409',message='account_deletion_storage_remaining'; end if;
  delete from public.core_chat_images where owner_id=actor;
 end if;
 value:=core_private.account_deletion_before_chat_photos(action,args);
 if action='assets' then
  select coalesce(jsonb_agg(jsonb_build_object('path',id::text||'.jpg','bucket','core-chat-images')),'[]') into images
   from public.core_chat_images where owner_id=actor;
  value:=jsonb_set(value,'{items}',(value->'items')||images);
 end if;
 return value;
end $$;
revoke all on function core_private.account_deletion(text,jsonb) from public,anon,authenticated;
grant execute on function core_private.account_deletion(text,jsonb) to authenticated;
