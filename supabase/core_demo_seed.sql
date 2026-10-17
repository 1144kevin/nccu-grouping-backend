-- Development only. The recipient is an existing profile; never changes its Auth data.
-- Re-running the same batch is a no-op, including after app cleanup.
do $seed$
declare
  owner uuid := '38eda2b4-cbd5-41ff-988f-c43542265620';
  batch_name text := 'showcase_20261017_v1';
  people uuid[] := array[gen_random_uuid(),gen_random_uuid(),gen_random_uuid(),gen_random_uuid()];
  titles text[] := array['便當晚餐：等待主揪審核','可頌早餐：可以編輯','手搖飲料：已經滿團','昨日午餐：已經截止','披薩聚餐：主揪取消','咖啡外帶：可以申請加入','麵包分享：免費領取'];
  capacities integer[] := array[5,4,4,3,3,4,3];
  prices text[] := array['95.00','65.00','50.00','90.00','120.00','60.00','0.00'];
  names text[] := array['【示範】小林','【示範】小陳','【示範】小黃','【示範】阿揪'];
  badges text[] := array['🍜','🥐','🧋','🍍'];
  post_ids uuid[] := '{}';
  post_id uuid; host_id uuid; value jsonb; deadline timestamptz; i integer; j integer;
begin
  if not exists(select 1 from public.core_profiles where id=owner) then
    raise exception 'Demo recipient profile does not exist';
  end if;
  perform pg_advisory_xact_lock(hashtext(batch_name));
  if exists(select 1 from core_private.demo_batches where batch=batch_name) then return; end if;
  for i in 1..4 loop
    -- These are inert fixture identities: no password, no sessions, no OAuth identity.
    insert into auth.users(id,aud,role,email,raw_app_meta_data,raw_user_meta_data,created_at,updated_at)
    values(people[i],'authenticated','authenticated',people[i]::text||'@example.test',
      jsonb_build_object('demo_batch',batch_name),jsonb_build_object('display_name',names[i]),now(),now());
    insert into public.core_profiles(id,display_name,bio,badge)
    values(people[i],names[i],'開發示範人物，無法登入，沒有真實訂購。',badges[i]);
  end loop;
  insert into core_private.demo_batches(batch,owner_id,people) values(batch_name,owner,people);
  for i in 1..7 loop
    host_id := case when i<=5 then owner else people[4] end;
    deadline := case when i=4 then now()-interval '1 day' else now()+interval '3 days'+i*interval '1 hour' end;
    value := jsonb_build_object('content','【示範】'||titles[i]||E'\n這是開發測試資料，方便操作拼單流程，不會真的下單或付款。',
      'tags',jsonb_build_array('開發示範',case when i=7 then '免費分享' else '一起拼單' end),
      'deadline_at',deadline,'capacity',capacities[i],'price_per_person',prices[i],
      'location',jsonb_build_object('name','政大校門口（示範集合點）','latitude',24.9876,'longitude',121.5760,'address','臺北市文山區指南路二段64號','source','openstreetmap'));
    insert into public.core_posts(author_id,payload,deadline_at,capacity,cancelled,request_key,original_payload,demo_batch,demo_owner_id)
    values(host_id,value,deadline,capacities[i],i=5,batch_name||'_post_'||i,value,batch_name,owner) returning id into post_id;
    post_ids := array_append(post_ids,post_id);
  end loop;
  for i in 1..3 loop
    insert into public.core_join_requests(post_id,requester_id,status,decided_at)
    values(post_ids[1],people[i],case when i=3 then 'pending' else 'accepted' end,case when i=3 then null else now() end);
    insert into public.core_join_requests(post_id,requester_id,status,decided_at)
    values(post_ids[3],people[i],'accepted',now());
  end loop;
  insert into public.core_join_requests(post_id,requester_id,status,decided_at)
  values(post_ids[4],people[1],'accepted',now()),(post_ids[6],people[1],'accepted',now());
end;
$seed$;
