-- Independent read-only history endpoint. Existing dispatcher is not replaced.
create function core_private.order_history(args jsonb) returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '8s' as $$
declare actor uuid := auth.uid(); result jsonb; value jsonb; page_size integer;
  term text; last_time timestamptz; last_id uuid; next_cursor text;
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false)) or
    not exists(select 1 from auth.sessions where id=(auth.jwt()->>'session_id')::uuid and user_id=actor
      and (not_after is null or not_after>clock_timestamp()))
  then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  if jsonb_typeof(args) is distinct from 'object' or args-array['limit','cursor','q','active_only']<>'{}'::jsonb then
    raise exception using errcode='PT422',message='invalid_query'; end if;
  page_size:=coalesce((args->>'limit')::integer,30); term:=btrim(coalesce(args->>'q',''));
  if page_size not between 1 and 50 or char_length(term)>100 then
    raise exception using errcode='PT422',message='invalid_query'; end if;
  if coalesce(args->>'cursor','')<>'' then
    value:=convert_from(decode(args->>'cursor','base64'),'UTF8')::jsonb;
    if jsonb_typeof(value) is distinct from 'array' or jsonb_array_length(value)<>2 then
      raise exception using errcode='PT422',message='invalid_cursor'; end if;
    last_time:=(value->>0)::timestamptz; last_id:=(value->>1)::uuid;
    if last_time is null or last_id is null then
      raise exception using errcode='PT422',message='invalid_cursor'; end if;
  end if;
  select coalesce(jsonb_agg(core_private.post_view(p.id,actor) order by p.created_at desc,p.id desc),'[]') into result
  from (
    select p.* from public.core_posts p join public.core_profiles u on u.id=p.author_id
    where (p.author_id=actor or exists (
      select 1 from public.core_join_requests j where j.post_id=p.id and j.requester_id=actor))
    and (p.cancelled or p.deadline_at<=clock_timestamp() or exists (
      select 1 from public.core_join_requests j where j.post_id=p.id and j.requester_id=actor
        and j.status in ('cancelled','rejected')))
    and (term='' or strpos(lower(p.payload->>'content'),lower(term))>0 or
      strpos(lower((p.payload->'tags')::text),lower(term))>0 or strpos(lower(u.display_name),lower(term))>0)
    and (not coalesce((args->>'active_only')::boolean,false) or (not p.cancelled and p.deadline_at>clock_timestamp()))
    and (last_time is null or (p.created_at,p.id)<(last_time,last_id))
    order by p.created_at desc,p.id desc limit page_size+1
  ) p;
  if jsonb_array_length(result)>page_size then
    value:=result->(page_size-1);
    next_cursor:=replace(encode(convert_to(jsonb_build_array(value->>'created_at',value->>'id')::text,'UTF8'),'base64'),E'\n','');
    result:=result-page_size;
  end if;
  return jsonb_build_object('items',result,'next_cursor',next_cursor);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range
  or character_not_in_repertoire or datetime_field_overflow or invalid_datetime_format then
  raise exception using errcode='PT422',message='invalid_query';
end;
$$;
revoke all on function core_private.order_history(jsonb) from public,anon,authenticated;
grant execute on function core_private.order_history(jsonb) to authenticated;
create function public.core_order_history(args jsonb default '{}') returns jsonb
language sql security invoker set search_path = '' as $$ select core_private.order_history(args); $$;
revoke all on function public.core_order_history(jsonb) from public,anon,authenticated;
grant execute on function public.core_order_history(jsonb) to authenticated;
