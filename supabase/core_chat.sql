-- Additive group text chat. No changes to existing auth/post dispatcher.
create table public.core_chat_messages (
  id uuid primary key default gen_random_uuid(),
  post_id uuid not null references public.core_posts(id) on delete cascade,
  sender_id uuid not null references public.core_profiles(id),
  content text not null check (char_length(btrim(content)) between 1 and 2000),
  client_key text not null check (char_length(client_key) between 12 and 100),
  created_at timestamptz not null default clock_timestamp(),
  unique(sender_id,client_key)
);
create index core_chat_post_time on public.core_chat_messages(post_id,created_at desc,id desc);
create index core_chat_sender_time on public.core_chat_messages(sender_id,created_at desc);
alter table public.core_chat_messages enable row level security;
-- Private text is only available through the live-session-checked RPC, including reads.
revoke all on public.core_chat_messages from public,anon,authenticated;

create function core_private.chat_message_view(message public.core_chat_messages) returns jsonb
language sql stable set search_path = '' as $$
  select jsonb_build_object('id',message.id,'post_id',message.post_id,'sender_id',message.sender_id,
    'sender_name',p.display_name,'sender_badge',p.badge,'content',message.content,'created_at',message.created_at)
  from public.core_profiles p where p.id=message.sender_id;
$$;
revoke all on function core_private.chat_message_view(public.core_chat_messages) from public,anon,authenticated;

create function core_private.chat(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '8s' as $$
declare actor uuid:=auth.uid(); post public.core_posts; message public.core_chat_messages;
  page_size integer; value jsonb; result jsonb; next_cursor text; last_time timestamptz; last_id uuid;
  content text; client_key text; allowed text[];
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false)) or
    not exists(select 1 from auth.sessions where id=(auth.jwt()->>'session_id')::uuid and user_id=actor
      and (not_after is null or not_after>clock_timestamp()))
  then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  allowed:=case action when 'list' then array['limit','cursor']
    when 'messages' then array['post_id','limit','cursor']
    when 'send' then array['post_id','content','client_key'] else null end;
  if allowed is null or jsonb_typeof(args) is distinct from 'object' or args-allowed<>'{}'::jsonb then
    raise exception using errcode='PT422',message='invalid_chat_input'; end if;
  page_size:=coalesce((args->>'limit')::integer,30);
  if page_size not between 1 and 50 then raise exception using errcode='PT422',message='invalid_query'; end if;
  if coalesce(args->>'cursor','')<>'' then
    value:=convert_from(decode(args->>'cursor','base64'),'UTF8')::jsonb;
    if jsonb_typeof(value) is distinct from 'array' or jsonb_array_length(value)<>2 then
      raise exception using errcode='PT422',message='invalid_cursor'; end if;
    last_time:=(value->>0)::timestamptz; last_id:=(value->>1)::uuid;
    if last_time is null or last_id is null then raise exception using errcode='PT422',message='invalid_cursor'; end if;
  end if;
  if action='list' then
    select coalesce(jsonb_agg(jsonb_build_object('post',core_private.post_view(p.id,actor),'last_message',(
      select core_private.chat_message_view(m) from public.core_chat_messages m where m.post_id=p.id
      order by m.created_at desc,m.id desc limit 1)) order by p.created_at desc,p.id desc),'[]') into result
    from (
      select p.* from public.core_posts p where (p.author_id=actor or exists (
        select 1 from public.core_join_requests j where j.post_id=p.id and j.requester_id=actor and j.status='accepted'))
      and (last_time is null or (p.created_at,p.id)<(last_time,last_id))
      order by p.created_at desc,p.id desc limit page_size+1
    ) p;
    if jsonb_array_length(result)>page_size then
      value:=result->(page_size-1)->'post';
      next_cursor:=replace(encode(convert_to(jsonb_build_array(value->>'created_at',value->>'id')::text,'UTF8'),'base64'),E'\n','');
      result:=result-page_size;
    end if;
    return jsonb_build_object('items',result,'next_cursor',next_cursor);
  end if;
  if action='send' then
    -- Serializes each sender's quota and idempotency across different groups.
    perform 1 from public.core_profiles where id=actor for update;
  end if;
  if action='send' then
    select * into post from public.core_posts where id=(args->>'post_id')::uuid for update;
  else
    select * into post from public.core_posts where id=(args->>'post_id')::uuid;
  end if;
  if not found then raise exception using errcode='PT404',message='post_not_found'; end if;
  if post.author_id<>actor and not exists(select 1 from public.core_join_requests
    where post_id=post.id and requester_id=actor and status='accepted')
  then raise exception using errcode='PT403',message='chat_members_only'; end if;
  if action='send' then
    if jsonb_typeof(args->'content') is distinct from 'string' or
      jsonb_typeof(args->'client_key') is distinct from 'string' then
      raise exception using errcode='PT422',message='invalid_chat_input'; end if;
    content:=btrim(args->>'content'); client_key:=args->>'client_key';
    if char_length(content) not between 1 and 2000 or char_length(client_key) not between 12 and 100 then
      raise exception using errcode='PT422',message='invalid_chat_input'; end if;
    select * into message from public.core_chat_messages where sender_id=actor and core_chat_messages.client_key=chat.client_key;
    if found then
      if message.post_id<>post.id or message.content<>content then
        raise exception using errcode='PT409',message='message_key_payload_conflict'; end if;
      return core_private.chat_message_view(message);
    end if;
    if post.cancelled then raise exception using errcode='PT409',message='chat_read_only'; end if;
    if (select count(*) from public.core_chat_messages where sender_id=actor and created_at>clock_timestamp()-interval '1 minute')>=30 then
      raise exception using errcode='PT429',message='chat_rate_limit'; end if;
    insert into public.core_chat_messages(post_id,sender_id,content,client_key)
      values(post.id,actor,content,client_key) returning * into message;
    return core_private.chat_message_view(message);
  end if;
  select coalesce(jsonb_agg(core_private.chat_message_view(m) order by m.created_at asc,m.id asc),'[]') into result from (
    select m.* from public.core_chat_messages m where m.post_id=post.id
    and (last_time is null or (m.created_at,m.id)<(last_time,last_id))
    order by m.created_at desc,m.id desc limit page_size+1
  ) m;
  if jsonb_array_length(result)>page_size then
    result:=result-0;
    value:=result->0;
    next_cursor:=replace(encode(convert_to(jsonb_build_array(value->>'created_at',value->>'id')::text,'UTF8'),'base64'),E'\n','');
  end if;
  return jsonb_build_object('post',core_private.post_view(post.id,actor),'messages',result,'next_cursor',next_cursor,'can_send',not post.cancelled);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range
  or character_not_in_repertoire or datetime_field_overflow or invalid_datetime_format then
  raise exception using errcode='PT422',message='invalid_chat_input';
end;
$$;
revoke all on function core_private.chat(text,jsonb) from public,anon,authenticated;
grant execute on function core_private.chat(text,jsonb) to authenticated;
create function public.core_chat(action text,args jsonb default '{}') returns jsonb
language sql security invoker set search_path = '' as $$ select core_private.chat(action,args); $$;
revoke all on function public.core_chat(text,jsonb) from public,anon,authenticated;
grant execute on function public.core_chat(text,jsonb) to authenticated;
