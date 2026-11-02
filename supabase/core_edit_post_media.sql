-- One transaction saves the shared composer fields and ordered gallery.
create function core_private.edit_post_media(args jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=core_private.account_guard(); target uuid; ids uuid[]; saved uuid[];
 asset public.core_media_assets; post public.core_posts; selected uuid; value jsonb; result jsonb; n integer;
begin
 if jsonb_typeof(args) is distinct from 'object' or args-array['post_id','value','image_ids','expected_version']<>'{}'::jsonb
   or jsonb_typeof(args->'image_ids') is distinct from 'array' or jsonb_array_length(args->'image_ids')>5
   or jsonb_typeof(args->'expected_version') is distinct from 'number' or (args->>'expected_version') !~ '^[1-9][0-9]*$'
 then raise exception using errcode='PT422',message='invalid_image_input'; end if;
 target:=(args->>'post_id')::uuid;
 select coalesce(array_agg(x.value::uuid order by x.ordinal),'{}'::uuid[]) into ids
   from jsonb_array_elements_text(args->'image_ids') with ordinality x(value,ordinal);
 if array_position(ids,null) is not null or (select count(distinct id) from unnest(ids) id)<>cardinality(ids) then
   raise exception using errcode='PT422',message='invalid_image_input'; end if;
 perform 1 from public.core_media_assets where id=any(ids) order by id for update;
 select * into post from public.core_posts where id=target for update;
 if not found then raise exception using errcode='PT404',message='post_not_found'; end if;
 if post.author_id<>actor then raise exception using errcode='PT403',message='host_only'; end if;
 result:=public.core_dispatch('post',jsonb_build_object('post_id',target));
 value:=core_private.post_input(args->'value');
 select coalesce(array_agg(asset_id order by position),'{}'::uuid[]) into saved from public.core_post_images where post_id=target;
 -- Retry a successful save whose HTTP response was lost, without another edit/version bump.
 if post.version=(args->>'expected_version')::integer+1 and post.payload=value and saved=ids then return result; end if;
 foreach selected in array ids loop
   select * into asset from public.core_media_assets where id=selected;
   if not found or asset.owner_id<>actor then raise exception using errcode='PT404',message='image_not_found'; end if;
   if exists(select 1 from public.core_post_images where asset_id=selected and post_id<>target) then
     raise exception using errcode='PT409',message='image_already_published'; end if;
   perform core_private.media_before_gallery('read',jsonb_build_object('image_id',selected));
 end loop;
 result:=public.core_dispatch('edit',jsonb_build_object('post_id',target,'value',value,'expected_version',args->'expected_version'));
 delete from public.core_post_images where post_id=target;
 if cardinality(ids)>0 then
   for n in 1..cardinality(ids) loop
     insert into public.core_post_images(post_id,asset_id,position) values(target,ids[n],n);
   end loop;
 end if;
 return core_private.post_view(target,actor);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range then
 raise exception using errcode='PT422',message='invalid_image_input';
end $$;
revoke all on function core_private.edit_post_media(jsonb) from public,anon;
grant execute on function core_private.edit_post_media(jsonb) to authenticated;
create function public.core_edit_post_media(args jsonb) returns jsonb
language sql set search_path='' as $$select core_private.edit_post_media(args)$$;
revoke all on function public.core_edit_post_media(jsonb) from public,anon;
grant execute on function public.core_edit_post_media(jsonb) to authenticated;
