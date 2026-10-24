begin;
alter table public.core_post_images add column position integer not null default 1 check(position between 1 and 5);
alter table public.core_post_images drop constraint core_post_images_pkey;
alter table public.core_post_images add primary key(post_id,position);

-- Preserve the live post view's reputation and status fields.
do $$ declare definition text; old_expression text := '(select pi.asset_id from public.core_post_images pi where pi.post_id=post.id)'; begin
 definition := pg_get_functiondef('core_private.post_view(uuid,uuid)'::regprocedure);
 if position(old_expression in definition)=0 then raise exception 'Unexpected post_view definition'; end if;
 execute replace(definition,old_expression,
  '(select pi.asset_id from public.core_post_images pi where pi.post_id=post.id order by pi.position limit 1),
   ''images'',coalesce((select jsonb_agg(jsonb_build_object(''id'',a.id,''width'',a.width,''height'',a.height) order by pi.position)
    from public.core_post_images pi join public.core_media_assets a on a.id=pi.asset_id where pi.post_id=post.id),''[]''::jsonb)');
end $$;

-- Keep all existing reservation, verification, Storage access and deletion behavior.
do $$ begin
 execute replace(pg_get_functiondef('core_private.media(text,jsonb)'::regprocedure),
  'core_private.media(', 'core_private.media_before_gallery(');
end $$;
revoke all on function core_private.media_before_gallery(text,jsonb) from public,anon,authenticated;

create or replace function core_private.media(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=core_private.media_actor(); ids uuid[]; saved uuid[]; asset public.core_media_assets;
 result jsonb; linked uuid; selected uuid; n integer;
begin
 if action not in ('publish','publish_many') then return core_private.media_before_gallery(action,args); end if;
 if actor is null then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
 if jsonb_typeof(args) is distinct from 'object' then raise exception using errcode='PT422',message='invalid_image_input'; end if;
 if action='publish' then
  if args-array['image_id','value','request_key']<>'{}'::jsonb then raise exception using errcode='PT422',message='invalid_image_input'; end if;
  ids:=array[(args->>'image_id')::uuid];
 else
  if args-array['image_ids','value','request_key']<>'{}'::jsonb or jsonb_typeof(args->'image_ids') is distinct from 'array' then
   raise exception using errcode='PT422',message='invalid_image_input'; end if;
  select array_agg(value::uuid order by ordinal) into ids from jsonb_array_elements_text(args->'image_ids') with ordinality x(value,ordinal);
 end if;
 if coalesce(cardinality(ids),0) not between 1 and 5 or array_position(ids,null) is not null or
  (select count(distinct i) from unnest(ids) i)<>cardinality(ids) then
  raise exception using errcode='PT422',message='invalid_image_input'; end if;
 -- Stable lock order serializes publish/delete and competing reuse of an asset.
 perform 1 from public.core_media_assets where id=any(ids) order by id for update;
 foreach selected in array ids loop
  select * into asset from public.core_media_assets where id=selected;
  if not found or asset.owner_id<>actor then raise exception using errcode='PT404',message='image_not_found'; end if;
  perform core_private.media_before_gallery('read',jsonb_build_object('image_id',selected));
 end loop;
 result:=public.core_dispatch('create',jsonb_build_object('value',args->'value','request_key',args->>'request_key'));
 linked:=(result->>'id')::uuid;
 -- Serialize complete galleries even when two different image sets reuse one post request.
 perform 1 from public.core_posts where id=linked for update;
 select array_agg(asset_id order by position) into saved from public.core_post_images where post_id=linked;
 if saved is not null then
  if saved<>ids then raise exception using errcode='PT409',message='image_key_conflict'; end if;
  return core_private.post_view(linked,actor);
 end if;
 if not coalesce((result->>'can_edit')::boolean,false) then raise exception using errcode='PT409',message='post_has_active_requests'; end if;
 if exists(select 1 from public.core_post_images where asset_id=any(ids)) then raise exception using errcode='PT409',message='image_already_published'; end if;
 for n in 1..cardinality(ids) loop
  insert into public.core_post_images(post_id,asset_id,position) values(linked,ids[n],n);
 end loop;
 return core_private.post_view(linked,actor);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range then
 raise exception using errcode='PT422',message='invalid_image_input';
end $$;
commit;
