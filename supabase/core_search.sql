-- Additive search; public profiles contain no Auth fields, messages enforce current membership.
create function core_private.search(args jsonb) returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '8s' as $$
declare actor uuid:=auth.uid(); scope text; keyword text; page_size integer;
  result jsonb; value jsonb; last_time timestamptz; last_id uuid; next_cursor text;
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false))
    or not exists(select 1 from auth.sessions where id=(auth.jwt()->>'session_id')::uuid and user_id=actor
      and (not_after is null or not_after>clock_timestamp())) then
    raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  if jsonb_typeof(args) is distinct from 'object' or args-array['scope','q','limit','cursor']<>'{}'::jsonb
    or jsonb_typeof(args->'scope') is distinct from 'string' or jsonb_typeof(args->'q') is distinct from 'string' then
    raise exception using errcode='PT422',message='invalid_search_query'; end if;
  scope:=args->>'scope'; keyword:=btrim(args->>'q'); page_size:=coalesce((args->>'limit')::integer,30);
  if scope not in ('users','messages','history') or char_length(keyword) not between 2 and 80
    or page_size not between 1 and 50 then
    raise exception using errcode='PT422',message='invalid_search_query'; end if;
  if coalesce(args->>'cursor','')<>'' then
    value:=convert_from(decode(args->>'cursor','base64'),'UTF8')::jsonb;
    if jsonb_typeof(value) is distinct from 'array' or jsonb_array_length(value)<>5
      or value->>0 is distinct from scope or value->>1 is distinct from keyword
      or value->>2 is distinct from actor::text then
      raise exception using errcode='PT422',message='invalid_search_cursor'; end if;
    last_time:=(value->>3)::timestamptz; last_id:=(value->>4)::uuid;
    if last_time is null or last_id is null then raise exception using errcode='PT422',message='invalid_search_cursor'; end if;
  end if;
  if scope='users' then
    select coalesce(jsonb_agg(jsonb_build_object('id',p.id,'kind','user','title',p.display_name,
      'detail',p.bio,'created_at',p.created_at) order by p.created_at desc,p.id desc),'[]') into result
    from (select * from public.core_profiles where
      (position(lower(keyword) in lower(display_name))>0 or position(lower(keyword) in lower(bio))>0)
      and (last_time is null or (created_at,id)<(last_time,last_id))
      order by created_at desc,id desc limit page_size+1) p;
  elsif scope='messages' then
    select coalesce(jsonb_agg(jsonb_build_object('id',m.id,'kind','message','title',sender.display_name,
      'detail',left(m.content,300),'post_id',m.post_id,'created_at',m.created_at)
      order by m.created_at desc,m.id desc),'[]') into result
    from (select cm.* from public.core_chat_messages cm join public.core_posts p on p.id=cm.post_id
      where (p.author_id=actor or exists(select 1 from public.core_join_requests j
        where j.post_id=p.id and j.requester_id=actor and j.status='accepted'))
      and position(lower(keyword) in lower(cm.content))>0
      and (last_time is null or (cm.created_at,cm.id)<(last_time,last_id))
      order by cm.created_at desc,cm.id desc limit page_size+1) m
      join public.core_profiles sender on sender.id=m.sender_id;
  else
    select coalesce(jsonb_agg(jsonb_build_object('id',p.id,'kind','history','title',left(p.payload->>'content',300),
      'detail',case when p.cancelled then '已取消' else '已截止' end,'post_id',p.id,'created_at',p.created_at)
      order by p.created_at desc,p.id desc),'[]') into result
    from (select cp.* from public.core_posts cp join public.core_profiles author on author.id=cp.author_id
      where (cp.author_id=actor or exists(select 1 from public.core_join_requests j where j.post_id=cp.id and j.requester_id=actor))
      and (cp.cancelled or cp.deadline_at<=clock_timestamp())
      and (position(lower(keyword) in lower(cp.payload->>'content'))>0
        or position(lower(keyword) in lower(author.display_name))>0
        or exists(select 1 from jsonb_array_elements_text(cp.payload->'tags') tag where position(lower(keyword) in lower(tag))>0))
      and (last_time is null or (cp.created_at,cp.id)<(last_time,last_id))
      order by cp.created_at desc,cp.id desc limit page_size+1) p;
  end if;
  if jsonb_array_length(result)>page_size then
    value:=result->(page_size-1);
    next_cursor:=replace(encode(convert_to(jsonb_build_array(scope,keyword,actor::text,value->>'created_at',value->>'id')::text,'UTF8'),'base64'),E'\n','');
    result:=result-page_size;
  end if;
  return jsonb_build_object('items',result,'next_cursor',next_cursor);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range
  or character_not_in_repertoire or datetime_field_overflow or invalid_datetime_format then
  raise exception using errcode='PT422',message='invalid_search_query';
end;
$$;
revoke all on function core_private.search(jsonb) from public,anon,authenticated;
grant execute on function core_private.search(jsonb) to authenticated;
create function public.core_search(args jsonb default '{}') returns jsonb
language sql security invoker set search_path = '' as $$ select core_private.search(args); $$;
revoke all on function public.core_search(jsonb) from public,anon,authenticated;
grant execute on function public.core_search(jsonb) to authenticated;
