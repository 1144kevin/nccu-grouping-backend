-- Latest request states for the host or requester. No triggers or mutation changes.
create function core_private.order_activity(args jsonb) returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '8s' as $$
declare actor uuid:=auth.uid(); result jsonb; value jsonb; page_size integer;
  last_time timestamptz; last_id uuid; next_cursor text;
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false)) or
    not exists(select 1 from auth.sessions where id=(auth.jwt()->>'session_id')::uuid and user_id=actor
      and (not_after is null or not_after>clock_timestamp()))
  then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  if jsonb_typeof(args) is distinct from 'object' or args-array['limit','cursor']<>'{}'::jsonb then
    raise exception using errcode='PT422',message='invalid_query'; end if;
  page_size:=coalesce((args->>'limit')::integer,30);
  if page_size not between 1 and 50 then raise exception using errcode='PT422',message='invalid_query'; end if;
  if coalesce(args->>'cursor','')<>'' then
    value:=convert_from(decode(args->>'cursor','base64'),'UTF8')::jsonb;
    if jsonb_typeof(value) is distinct from 'array' or jsonb_array_length(value)<>2 then
      raise exception using errcode='PT422',message='invalid_cursor'; end if;
    last_time:=(value->>0)::timestamptz; last_id:=(value->>1)::uuid;
    if last_time is null or last_id is null then raise exception using errcode='PT422',message='invalid_cursor'; end if;
  end if;
  select coalesce(jsonb_agg(jsonb_build_object('id',j.id,'post_id',j.post_id,'post_content',j.content,
    'requester_id',j.requester_id,'requester_name',j.display_name,'status',j.status,
    'kind',case when j.author_id=actor then 'hosted_request' else 'my_request' end,
    'occurred_at',j.occurred_at,'post_cancelled',j.cancelled,'post_expired',j.deadline_at<=clock_timestamp())
    order by j.occurred_at desc,j.id desc),'[]') into result from (
    select j.id,j.post_id,j.requester_id,j.status,coalesce(j.decided_at,j.created_at) as occurred_at,
      p.author_id,p.payload->>'content' as content,p.cancelled,p.deadline_at,u.display_name
    from public.core_join_requests j join public.core_posts p on p.id=j.post_id
    join public.core_profiles u on u.id=j.requester_id
    where (p.author_id=actor or j.requester_id=actor)
      and (last_time is null or (coalesce(j.decided_at,j.created_at),j.id)<(last_time,last_id))
    order by coalesce(j.decided_at,j.created_at) desc,j.id desc limit page_size+1
  ) j;
  if jsonb_array_length(result)>page_size then
    value:=result->(page_size-1);
    next_cursor:=replace(encode(convert_to(jsonb_build_array(value->>'occurred_at',value->>'id')::text,'UTF8'),'base64'),E'\n','');
    result:=result-page_size;
  end if;
  return jsonb_build_object('items',result,'next_cursor',next_cursor);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range
  or character_not_in_repertoire or datetime_field_overflow or invalid_datetime_format then
  raise exception using errcode='PT422',message='invalid_query';
end;
$$;
revoke all on function core_private.order_activity(jsonb) from public,anon,authenticated;
grant execute on function core_private.order_activity(jsonb) to authenticated;
create function public.core_order_activity(args jsonb default '{}') returns jsonb
language sql security invoker set search_path = '' as $$ select core_private.order_activity(args); $$;
revoke all on function public.core_order_activity(jsonb) from public,anon,authenticated;
grant execute on function public.core_order_activity(jsonb) to authenticated;
