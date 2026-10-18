-- Additive account preferences and private report inbox. No original RPC replaced.
create table public.core_account_preferences (
  owner_id uuid primary key references public.core_profiles(id) on delete cascade,
  payload jsonb not null,
  version integer not null check(version>0),
  updated_at timestamptz not null default clock_timestamp()
);
create table public.core_inbox_preferences (
  owner_id uuid not null references public.core_profiles(id) on delete cascade,
  post_id uuid not null references public.core_posts(id) on delete cascade,
  muted boolean not null default false,
  archived boolean not null default false,
  updated_at timestamptz not null default clock_timestamp(),
  primary key(owner_id,post_id)
);
create index core_inbox_preferences_post on public.core_inbox_preferences(post_id);
create table public.core_reports (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references public.core_profiles(id) on delete cascade,
  request_key text not null check(char_length(request_key) between 12 and 100),
  payload jsonb not null,
  status text not null default 'received' check(status='received'),
  created_at timestamptz not null default clock_timestamp(),
  unique(owner_id,request_key)
);
create index core_reports_owner_time on public.core_reports(owner_id,created_at desc,id desc);
alter table public.core_account_preferences enable row level security;
alter table public.core_inbox_preferences enable row level security;
alter table public.core_reports enable row level security;
revoke all on public.core_account_preferences,public.core_inbox_preferences,public.core_reports
  from public,anon,authenticated;

create function core_private.engagement(action text,args jsonb) returns jsonb
language plpgsql security definer set search_path = '' set statement_timeout = '8s' as $$
declare actor uuid:=auth.uid(); result jsonb; value jsonb; existing public.core_reports;
  prefs public.core_account_preferences; allowed text[]; tag jsonb; target uuid; parent uuid;
  keys text[]:=array['joinRequests','messages','recommendations','deadlineReminders','stealthInvites'];
  defaults jsonb:='{"notifications":{"joinRequests":true,"messages":true,"recommendations":false,"deadlineReminders":false,"stealthInvites":true},"preferred_tags":[]}';
begin
  if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false))
    or not exists(select 1 from auth.sessions where id=(auth.jwt()->>'session_id')::uuid and user_id=actor
      and (not_after is null or not_after>clock_timestamp())) then
    raise exception using errcode='PT401',message='invalid_or_expired_session';
  end if;
  allowed:=case action
    when 'preferences_get' then array[]::text[] when 'preferences_put' then array['expected_version','value']
    when 'inbox_list' then array[]::text[] when 'inbox_put' then array['post_id','muted','archived']
    when 'reports' then array[]::text[] when 'report' then array['value','request_key'] else null end;
  if allowed is null or jsonb_typeof(args) is distinct from 'object' or args-allowed<>'{}'::jsonb then
    raise exception using errcode='PT422',message='invalid_account_input'; end if;
  if action in ('preferences_put','inbox_put','report') then
    perform pg_advisory_xact_lock(hashtextextended('core-engagement:'||actor::text,0));
  end if;
  if action in ('preferences_get','preferences_put') then
    select * into prefs from public.core_account_preferences where owner_id=actor;
    if action='preferences_get' then
      return coalesce(prefs.payload,defaults)||jsonb_build_object('version',coalesce(prefs.version,0));
    end if;
    value:=args->'value';
    if jsonb_typeof(args->'expected_version') is distinct from 'number'
      or (args->>'expected_version') !~ '^[0-9]{1,10}$'
      or jsonb_typeof(value) is distinct from 'object'
      or value-array['notifications','preferred_tags']<>'{}'::jsonb
      or jsonb_typeof(value->'notifications') is distinct from 'object'
      or value->'notifications'-keys<>'{}'::jsonb
      or not (value->'notifications' ?& keys)
      or exists(select 1 from jsonb_each(value->'notifications') n where jsonb_typeof(n.value)<>'boolean')
      or jsonb_typeof(value->'preferred_tags') is distinct from 'array'
      or jsonb_array_length(value->'preferred_tags')>24 then
      raise exception using errcode='PT422',message='invalid_account_input'; end if;
    for tag in select * from jsonb_array_elements(value->'preferred_tags') loop
      if jsonb_typeof(tag)<>'string' or char_length(btrim(tag#>>'{}')) not between 1 and 32 then
        raise exception using errcode='PT422',message='invalid_account_input'; end if;
    end loop;
    if (select count(distinct t#>>'{}') from jsonb_array_elements(value->'preferred_tags') t)
      <>jsonb_array_length(value->'preferred_tags') then
      raise exception using errcode='PT422',message='invalid_account_input'; end if;
    if (args->>'expected_version')::integer<>coalesce(prefs.version,0) then
      raise exception using errcode='PT409',message='preferences_changed_reload'; end if;
    if prefs.owner_id is not null and prefs.payload=value then
      return prefs.payload||jsonb_build_object('version',prefs.version); end if;
    insert into public.core_account_preferences(owner_id,payload,version)
      values(actor,value,1) on conflict(owner_id) do update
      set payload=excluded.payload,version=core_account_preferences.version+1,updated_at=clock_timestamp()
      returning * into prefs;
    return prefs.payload||jsonb_build_object('version',prefs.version);
  end if;
  if action='inbox_list' then
    select coalesce(jsonb_agg(jsonb_build_object('post_id',i.post_id,'muted',i.muted,'archived',i.archived)
      order by i.updated_at desc,i.post_id),'[]') into result
    from public.core_inbox_preferences i join public.core_posts p on p.id=i.post_id where i.owner_id=actor
      and (p.author_id=actor or exists(select 1 from public.core_join_requests j
        where j.post_id=p.id and j.requester_id=actor and j.status='accepted'));
    return jsonb_build_object('items',result);
  end if;
  if action='inbox_put' then
    target:=(args->>'post_id')::uuid;
    if jsonb_typeof(args->'muted') is distinct from 'boolean'
      or jsonb_typeof(args->'archived') is distinct from 'boolean' then
      raise exception using errcode='PT422',message='invalid_account_input'; end if;
    if not exists(select 1 from public.core_posts p where p.id=target and
      (p.author_id=actor or exists(select 1 from public.core_join_requests j
        where j.post_id=p.id and j.requester_id=actor and j.status='accepted'))) then
      raise exception using errcode='PT403',message='chat_members_only'; end if;
    if not exists(select 1 from public.core_inbox_preferences where owner_id=actor and post_id=target)
      and (select count(*) from public.core_inbox_preferences where owner_id=actor)>=200 then
      raise exception using errcode='PT409',message='inbox_preference_limit'; end if;
    insert into public.core_inbox_preferences(owner_id,post_id,muted,archived)
      values(actor,target,(args->>'muted')::boolean,(args->>'archived')::boolean)
      on conflict(owner_id,post_id) do update set muted=excluded.muted,archived=excluded.archived,
      updated_at=clock_timestamp();
    return jsonb_build_object('post_id',target,'muted',args->'muted','archived',args->'archived');
  end if;
  if action='reports' then
    select coalesce(jsonb_agg(r.payload||jsonb_build_object('id',r.id,'status',r.status,'created_at',r.created_at)
      order by r.created_at desc,r.id desc),'[]') into result
    from(select * from public.core_reports where owner_id=actor order by created_at desc,id desc limit 50) r;
    return jsonb_build_object('items',result);
  end if;
  value:=args->'value';
  if jsonb_typeof(args->'request_key') is distinct from 'string'
    or char_length(args->>'request_key') not between 12 and 100
    or jsonb_typeof(value) is distinct from 'object'
    or value-array['kind','target_id','reason','detail','platform','app_version']<>'{}'::jsonb
    or not coalesce(value->>'kind' in ('feedback','post','message','user'),false)
    or not coalesce(value->>'reason' in ('other','spam','harassment','inappropriate','fraud'),false)
    or not coalesce(value->>'platform' in ('web','ios','android','unknown'),false)
    or jsonb_typeof(value->'detail') is distinct from 'string'
    or char_length(btrim(value->>'detail')) not between 10 and 1000
    or jsonb_typeof(value->'app_version') is distinct from 'string'
    or char_length(value->>'app_version')>40 then
    raise exception using errcode='PT422',message='invalid_report_input'; end if;
  target:=(value->>'target_id')::uuid;
  if (value->>'kind'='feedback')<>(target is null) then
    raise exception using errcode='PT422',message='invalid_report_input'; end if;
  select * into existing from public.core_reports where owner_id=actor and request_key=args->>'request_key';
  if found then
    if existing.payload<>value then raise exception using errcode='PT409',message='report_key_conflict'; end if;
    return existing.payload||jsonb_build_object('id',existing.id,'status',existing.status,'created_at',existing.created_at);
  end if;
  if value->>'kind'='post' and not exists(select 1 from public.core_posts where id=target) then
    raise exception using errcode='PT404',message='report_target_unavailable'; end if;
  if value->>'kind'='user' and (target=actor or not exists(select 1 from public.core_profiles where id=target)) then
    raise exception using errcode='PT404',message='report_target_unavailable'; end if;
  if value->>'kind'='message' then
    select post_id into parent from public.core_chat_messages where id=target;
    if parent is null or not exists(select 1 from public.core_posts p where p.id=parent and
      (p.author_id=actor or exists(select 1 from public.core_join_requests j
        where j.post_id=p.id and j.requester_id=actor and j.status='accepted'))) then
      raise exception using errcode='PT404',message='report_target_unavailable'; end if;
  end if;
  if (select count(*) from public.core_reports where owner_id=actor
    and created_at>clock_timestamp()-interval '1 hour')>=5 then
    raise exception using errcode='PT429',message='report_rate_limit'; end if;
  insert into public.core_reports(owner_id,request_key,payload) values(actor,args->>'request_key',value)
    returning * into existing;
  return existing.payload||jsonb_build_object('id',existing.id,'status',existing.status,'created_at',existing.created_at);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range then
  raise exception using errcode='PT422',message='invalid_account_input';
end;
$$;
revoke all on function core_private.engagement(text,jsonb) from public,anon,authenticated;
grant execute on function core_private.engagement(text,jsonb) to authenticated;
create function public.core_engagement(action text,args jsonb default '{}') returns jsonb
language sql security invoker set search_path = '' as $$ select core_private.engagement(action,args); $$;
revoke all on function public.core_engagement(text,jsonb) from public,anon,authenticated;
grant execute on function public.core_engagement(text,jsonb) to authenticated;
