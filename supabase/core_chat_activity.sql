-- Independent account-scoped read cursors and real badge counts.
create table public.core_chat_reads (
  post_id uuid not null references public.core_posts(id) on delete cascade,
  viewer_id uuid not null references public.core_profiles(id),
  last_read_at timestamptz not null,
  last_read_id uuid not null,
  updated_at timestamptz not null default clock_timestamp(),
  primary key(post_id,viewer_id)
);
create index core_chat_reads_viewer on public.core_chat_reads(viewer_id,post_id);
alter table public.core_chat_reads enable row level security;
revoke all on public.core_chat_reads from public,anon,authenticated;

create function core_private.chat_activity(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '8s' as $$
declare actor uuid:=auth.uid(); post public.core_posts; message public.core_chat_messages;
  result jsonb; total integer; pending integer; allowed text[];
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false)) or
    not exists(select 1 from auth.sessions where id=(auth.jwt()->>'session_id')::uuid and user_id=actor
      and (not_after is null or not_after>clock_timestamp()))
  then raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
  allowed:=case action when 'status' then array[]::text[] when 'read' then array['post_id','message_id'] else null end;
  if allowed is null or jsonb_typeof(args) is distinct from 'object' or args-allowed<>'{}'::jsonb then
    raise exception using errcode='PT422',message='invalid_chat_input'; end if;
  if action='read' then
    select * into post from public.core_posts where id=(args->>'post_id')::uuid;
    if not found then raise exception using errcode='PT404',message='post_not_found'; end if;
    if post.author_id<>actor and not exists(select 1 from public.core_join_requests
      where post_id=post.id and requester_id=actor and status='accepted')
    then raise exception using errcode='PT403',message='chat_members_only'; end if;
    select * into message from public.core_chat_messages where id=(args->>'message_id')::uuid and post_id=post.id;
    if not found then raise exception using errcode='PT404',message='message_not_found'; end if;
    -- Use the displayed message's database timestamp, never a client-supplied clock.
    insert into public.core_chat_reads(post_id,viewer_id,last_read_at,last_read_id)
      values(post.id,actor,message.created_at,message.id)
      on conflict(post_id,viewer_id) do update set last_read_at=excluded.last_read_at,
        last_read_id=excluded.last_read_id,updated_at=clock_timestamp()
      where (core_chat_reads.last_read_at,core_chat_reads.last_read_id)<(excluded.last_read_at,excluded.last_read_id);
    return jsonb_build_object('ok',true);
  end if;
  select coalesce(jsonb_agg(jsonb_build_object('post_id',p.id,'unread_count',counts.n)),'[]'),
    coalesce(sum(counts.n),0)::integer into result,total
  from public.core_posts p
  left join public.core_chat_reads r on r.post_id=p.id and r.viewer_id=actor
  cross join lateral (
    select count(*)::integer n from public.core_chat_messages m where m.post_id=p.id and m.sender_id<>actor
      and (r.last_read_at is null or (m.created_at,m.id)>(r.last_read_at,r.last_read_id))
  ) counts
  where p.author_id=actor or exists(select 1 from public.core_join_requests j
    where j.post_id=p.id and j.requester_id=actor and j.status='accepted');
  select count(*)::integer into pending from public.core_join_requests j join public.core_posts p on p.id=j.post_id
    where p.author_id=actor and not p.cancelled and p.deadline_at>clock_timestamp() and j.status='pending';
  return jsonb_build_object('groups',result,'total_unread',total,'pending_requests',pending);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range then
  raise exception using errcode='PT422',message='invalid_chat_input';
end;
$$;
revoke all on function core_private.chat_activity(text,jsonb) from public,anon,authenticated;
grant execute on function core_private.chat_activity(text,jsonb) to authenticated;
create function public.core_chat_activity(action text,args jsonb default '{}') returns jsonb
language sql security invoker set search_path = '' as $$ select core_private.chat_activity(action,args); $$;
revoke all on function public.core_chat_activity(text,jsonb) from public,anon,authenticated;
grant execute on function public.core_chat_activity(text,jsonb) to authenticated;
