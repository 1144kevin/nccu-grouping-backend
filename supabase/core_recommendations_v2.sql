-- Development upgrade: preserve the existing public safety wrapper and table grants.
-- Explainable rules only; no model inference or hidden behavioral tracking.
create or replace function core_private.recommendation_feed(args jsonb) returns jsonb
language plpgsql security definer set search_path='' set statement_timeout='8s' as $$
declare actor uuid:=auth.uid(); preferences text[]; preference_version integer; page_size integer;
 result jsonb; cv jsonb; last_score integer; last_time timestamptz; last_id uuid;
 next_cursor text; ts timestamptz:=clock_timestamp(); asof timestamptz:=ts;
 inventory_hash text; previous_hash text;
begin
 if actor is null or not exists(select 1 from auth.users where id=actor and not coalesce(is_anonymous,false))
 or not exists(select 1 from auth.sessions where user_id=actor and id=(auth.jwt()->>'session_id')::uuid
 and (not_after is null or not_after>ts)) then
 raise exception using errcode='PT401',message='invalid_or_expired_session'; end if;
 if jsonb_typeof(args) is distinct from 'object' or args-array['limit','cursor']<>'{}'::jsonb then
 raise exception using errcode='PT422',message='invalid_query'; end if;
 page_size:=coalesce((args->>'limit')::integer,30);
 if page_size not between 1 and 50 then raise exception using errcode='PT422',message='invalid_query'; end if;
 select array(select jsonb_array_elements_text(payload->'preferred_tags')),version
 into preferences,preference_version from public.core_account_preferences where owner_id=actor;
 preferences:=coalesce(preferences,array[]::text[]); preference_version:=coalesce(preference_version,0);
 if coalesce(args->>'cursor','')<>'' then
  cv:=convert_from(decode(args->>'cursor','base64'),'UTF8')::jsonb;
  if jsonb_typeof(cv) is distinct from 'array' or jsonb_array_length(cv)<>8 then
   raise exception using errcode='PT409',message='recommendations_changed_reload'; end if;
  if (cv->>0)::integer is distinct from 2 or (cv->>1)::uuid is distinct from actor
  or (cv->>3)::integer is distinct from preference_version then
   raise exception using errcode='PT409',message='recommendations_changed_reload'; end if;
  asof:=(cv->>2)::timestamptz; previous_hash:=cv->>4;
  last_score:=(cv->>5)::integer; last_time:=(cv->>6)::timestamptz; last_id:=(cv->>7)::uuid;
  if asof is null or previous_hash is null or last_score is null or last_time is null or last_id is null then
   raise exception using errcode='PT422',message='invalid_query'; end if;
  if asof>ts or asof<ts-interval '15 minutes' then
   raise exception using errcode='PT409',message='recommendations_changed_reload'; end if;
 end if;
 with eligible as materialized (
  select p.*,p.capacity-1-counts.accepted as remaining
  from public.core_posts p cross join lateral (
   select count(*)::integer as accepted from public.core_join_requests j
   where j.post_id=p.id and j.status='accepted'
  ) counts
  where not p.cancelled and p.deadline_at>ts and p.capacity>1+counts.accepted
  and not core_private.users_blocked(actor,p.author_id)
  and not exists(select 1 from public.core_saved_post_preferences h
   where h.owner_id=actor and h.post_id=p.id and h.hidden)
 ), matched as (
  select e.*,
   array(select distinct t from jsonb_array_elements_text(e.payload->'tags') t
    where t=any(preferences) order by t) as matched_tags,
   array(select distinct t from unnest(preferences) t
    where t<>'' and position(t in coalesce(e.payload->'location'->>'name',''))>0 order by t) as matched_locations
  from eligible e
 ), features as (
  select m.*,
   least(cardinality(matched_tags),3)*20+least(cardinality(matched_locations),2)*8 as preference_score,
   case when remaining=1 then 12 when remaining=2 then 8 when remaining<=5 then 4 else 0 end as seats_score,
   case when deadline_at<=asof+interval '30 minutes' then 8
    when deadline_at<=asof+interval '2 hours' then 6
    when deadline_at<=asof+interval '6 hours' then 3 else 0 end as deadline_score
  from matched m
 ), scored as (
  select f.*,preference_score+seats_score+deadline_score as score,
   (case when cardinality(matched_tags)>0 then jsonb_build_array(jsonb_build_object(
    'code','preferred_tags','text','符合你的標籤偏好：'||array_to_string(matched_tags[1:3],'、'))) else '[]'::jsonb end)
   ||(case when cardinality(matched_locations)>0 then jsonb_build_array(jsonb_build_object(
    'code','preferred_location','text','集合地點符合偏好詞：'||array_to_string(matched_locations[1:2],'、'))) else '[]'::jsonb end)
   ||jsonb_build_array(jsonb_build_object('code','remaining_seats','text','尚有 '||remaining||' 個名額'))
   ||jsonb_build_array(jsonb_build_object('code','deadline','text',case
    when deadline_score=8 then '30 分鐘內截止，請先確認時間'
    when deadline_score=6 then '2 小時內截止'
    when deadline_score=3 then '6 小時內截止' else '截止時間較充裕' end)) as reasons
  from features f
 ), inventory as (
  select md5(coalesce(string_agg(id::text||':'||version||':'||remaining,',' order by id),'')) as hash
  from eligible
 ), page as (
  select * from scored where last_score is null or (score,created_at,id)<(last_score,last_time,last_id)
  order by score desc,created_at desc,id desc limit page_size+1
 ) select coalesce(jsonb_agg(jsonb_build_object('score',score,
  'post',core_private.post_view(id,actor)||jsonb_build_object('recommendation',jsonb_build_object(
   'version','rules_v2','score',score,'remaining_seats',remaining,'as_of',asof,'reasons',reasons)))
  order by score desc,created_at desc,id desc),'[]'),(select hash from inventory)
 into result,inventory_hash from page;
 if previous_hash is not null and previous_hash<>inventory_hash then
  raise exception using errcode='PT409',message='recommendations_changed_reload'; end if;
 if jsonb_array_length(result)>page_size then
  cv:=result->(page_size-1);
  next_cursor:=replace(encode(convert_to(jsonb_build_array(2,actor,asof,preference_version,inventory_hash,
   cv->'score',cv->'post'->>'created_at',cv->'post'->>'id')::text,'UTF8'),'base64'),E'\n','');
  result:=result-page_size;
 end if;
 select coalesce(jsonb_agg(item->'post' order by ordinal),'[]') into result
 from jsonb_array_elements(result) with ordinality x(item,ordinal);
 return jsonb_build_object('items',result,'next_cursor',next_cursor);
exception when invalid_text_representation or invalid_parameter_value or numeric_value_out_of_range
 or character_not_in_repertoire or datetime_field_overflow or invalid_datetime_format then
 raise exception using errcode='PT422',message='invalid_query';
end;
$$;
revoke all on function core_private.recommendation_feed(jsonb) from public,anon,authenticated;
grant execute on function core_private.recommendation_feed(jsonb) to authenticated;
