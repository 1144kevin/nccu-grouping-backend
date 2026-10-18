-- Explainable preference ranking. No learned score, hidden feed data or paid inference.
create function core_private.recommendation_feed(args jsonb) returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '8s' as $$
declare actor uuid:=auth.uid(); preferences text[]; preference_version integer; page_size integer;
  result jsonb; cursor_value jsonb; last_score integer; last_time timestamptz; last_id uuid;
  next_cursor text;
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false))
    or not exists(select 1 from auth.sessions where id=(auth.jwt()->>'session_id')::uuid and user_id=actor
      and (not_after is null or not_after>clock_timestamp())) then
    raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  if jsonb_typeof(args) is distinct from 'object' or args-array['limit','cursor']<>'{}'::jsonb then
    raise exception using errcode='PT422',message='invalid_query'; end if;
  page_size:=coalesce((args->>'limit')::integer,30);
  if page_size not between 1 and 50 then raise exception using errcode='PT422',message='invalid_query'; end if;
  select coalesce(array(select jsonb_array_elements_text(payload->'preferred_tags')),array[]::text[]),version
    into preferences,preference_version from public.core_account_preferences where owner_id=actor;
  preferences:=coalesce(preferences,array[]::text[]); preference_version:=coalesce(preference_version,0);
  if coalesce(args->>'cursor','')<>'' then
    cursor_value:=convert_from(decode(args->>'cursor','base64'),'UTF8')::jsonb;
    if jsonb_typeof(cursor_value) is distinct from 'array' or jsonb_array_length(cursor_value)<>4 then
      raise exception using errcode='PT422',message='invalid_cursor'; end if;
    last_score:=(cursor_value->>0)::integer;last_time:=(cursor_value->>1)::timestamptz;
    last_id:=(cursor_value->>2)::uuid;
    if last_score is null or last_time is null or last_id is null or (cursor_value->>3)::integer is null then
      raise exception using errcode='PT422',message='invalid_cursor'; end if;
    if (cursor_value->>3)::integer<>preference_version then
      raise exception using errcode='PT409',message='recommendations_changed_reload'; end if;
  end if;
  with scored as (
    select p.id,p.created_at,
      (select count(*)::integer*2 from jsonb_array_elements_text(p.payload->'tags') t
        where t=any(preferences))+
      (select count(*)::integer from unnest(preferences) t
        where position(t in coalesce(p.payload->'location'->>'name',''))>0) as score
    from public.core_posts p where not p.cancelled and p.deadline_at>clock_timestamp()
  ), page as (
    select * from scored where last_score is null or (score,created_at,id)<(last_score,last_time,last_id)
      order by score desc,created_at desc,id desc limit page_size+1
  ) select coalesce(jsonb_agg(jsonb_build_object('post',core_private.post_view(id,actor),'score',score)
      order by score desc,created_at desc,id desc),'[]') into result from page;
  if jsonb_array_length(result)>page_size then
    cursor_value:=result->(page_size-1);
    next_cursor:=replace(encode(convert_to(jsonb_build_array(cursor_value->'score',
      cursor_value->'post'->>'created_at',cursor_value->'post'->>'id',preference_version)::text,'UTF8'),'base64'),E'\n','');
    result:=result-page_size;
  end if;
  select coalesce(jsonb_agg(item->'post' order by ordinal),'[]') into result
    from jsonb_array_elements(result) with ordinality as x(item,ordinal);
  return jsonb_build_object('items',result,'next_cursor',next_cursor);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range
  or character_not_in_repertoire or datetime_field_overflow or invalid_datetime_format then
  raise exception using errcode='PT422',message='invalid_query';
end;
$$;
revoke all on function core_private.recommendation_feed(jsonb) from public,anon,authenticated;
grant execute on function core_private.recommendation_feed(jsonb) to authenticated;
create function public.core_recommendation_feed(args jsonb default '{}') returns jsonb
language sql security invoker set search_path = '' as $$ select core_private.recommendation_feed(args); $$;
revoke all on function public.core_recommendation_feed(jsonb) from public,anon,authenticated;
grant execute on function public.core_recommendation_feed(jsonb) to authenticated;
