-- Only explicitly shared posts can be read without an account. No anonymous listing.
create table core_private.post_shares (
  token text primary key default replace(gen_random_uuid()::text,'-','') || replace(gen_random_uuid()::text,'-',''),
  post_id uuid not null unique references public.core_posts(id) on delete cascade,
  created_at timestamptz not null default clock_timestamp(),
  check(token ~ '^[0-9a-f]{64}$')
);
alter table core_private.post_shares enable row level security;
revoke all on core_private.post_shares from public,anon,authenticated;

create function public.core_post_share(action text,args jsonb default '{}') returns jsonb
language plpgsql security definer set search_path='' as $$
declare actor uuid; target uuid; v_token text; post public.core_posts; v jsonb; asset public.core_media_assets;
begin
 if action='create' then
  actor:=core_private.account_guard();
  target:=(args->>'post_id')::uuid;
  if not core_private.post_accessible(actor,target) then
   raise exception using errcode='PT404',message='shared_post_not_found'; end if;
 else
  v_token:=args->>'token';
  if v_token is null or v_token !~ '^[0-9a-f]{64}$' then
   raise exception using errcode='PT404',message='shared_post_not_found'; end if;
  select s.post_id into target from core_private.post_shares s where s.token=v_token;
 end if;
 select * into post from public.core_posts p where p.id=target;
 if not found or post.cancelled or core_private.account_pending(post.author_id)
   or exists(select 1 from public.core_stealth_proposals s where s.formed_post_id=post.id) then
  raise exception using errcode='PT404',message='shared_post_not_found'; end if;
 if action='create' then
  insert into core_private.post_shares(post_id) values(post.id)
   on conflict(post_id) do update set post_id=excluded.post_id
   returning post_shares.token into v_token;
  return jsonb_build_object('token',v_token);
 elsif action='read' then
  v:=core_private.post_view(post.id,null);
  return jsonb_build_object('id',post.id,'author_name',v->'author_name',
   'author_badge',v->'author_badge','author_level',v->'author_level',
   'content',v->'content','tags',v->'tags','created_at',post.created_at,
   'deadline_at',post.deadline_at,'capacity',post.capacity,'joined',v->'joined',
   'status',v->'status','price_per_person',v->'price_per_person',
   'location_name',post.payload->'location'->>'name','images',v->'images');
 elsif action='image' then
  select a.* into asset from public.core_post_images pi join public.core_media_assets a on a.id=pi.asset_id
   where pi.post_id=post.id and a.id=(args->>'image_id')::uuid and a.state='ready';
  if not found then raise exception using errcode='PT404',message='shared_post_not_found'; end if;
  return jsonb_build_object('object_path',asset.owner_id::text||'/'||asset.id::text||'.jpg');
 end if;
 raise exception using errcode='PT422',message='invalid_share_action';
end $$;
revoke all on function public.core_post_share(text,jsonb) from public,anon,authenticated;
grant execute on function public.core_post_share(text,jsonb) to anon,authenticated;

-- Storage remains private. A guest read must present this exact share capability,
-- and the asset must still belong to that visible post (never a draft or chat photo).
create function public.core_shared_media_access(object_name text) returns boolean
language plpgsql stable security definer set search_path='' as $$
declare v_token text := (nullif(current_setting('request.headers',true),'')::jsonb)->>'x-post-share';
begin
 if v_token is null or v_token !~ '^[0-9a-f]{64}$' then return false; end if;
 return exists(select 1 from core_private.post_shares s
  join public.core_posts p on p.id=s.post_id
  join public.core_post_images pi on pi.post_id=p.id
  join public.core_media_assets a on a.id=pi.asset_id
  join storage.objects o on o.id=a.verified_object_id and o.bucket_id='core-post-images'
  where s.token=v_token and not p.cancelled and not core_private.account_pending(p.author_id)
   and not exists(select 1 from public.core_stealth_proposals sp where sp.formed_post_id=p.id)
   and a.state='ready' and o.name=object_name
   and object_name=a.owner_id::text||'/'||a.id::text||'.jpg');
end $$;
revoke all on function public.core_shared_media_access(text) from public,anon,authenticated;
grant execute on function public.core_shared_media_access(text) to anon,authenticated;
create policy core_images_shared_read on storage.objects for select to anon
 using(bucket_id='core-post-images' and public.core_shared_media_access(name));
