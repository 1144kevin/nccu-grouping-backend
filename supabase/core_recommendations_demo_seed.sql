-- Explicit development-only data operation, not an application migration.
-- 120 posts / 12 inert identities / 240 messages; never alters existing Auth users.
-- Idempotent: this batch remains consumed even after the owner clears its demo posts.
do $seed$
declare batch_name text:='showcase_20261019_recommendations_v2'; owner uuid;
 people uuid[]:='{}'; host uuid; post_id uuid; peer uuid; value jsonb; deadline timestamptz;
 ts timestamptz:=clock_timestamp(); i integer; j integer; cap integer; accepted integer; mode integer;
 food text[]:=array['便當','披薩','咖啡','麵包','飲料','蔬食','甜點','宵夜'];
 places text[]:=array['政大正門','政大東側門','政大南門','校內圖書館旁','指南路口','宿舍入口'];
 hours integer[]:=array[1,4,12,24,72,168];
begin
 perform pg_advisory_xact_lock(hashtext(batch_name));
 if exists(select 1 from core_private.demo_batches where batch=batch_name) then return; end if;
 select owner_id into owner from core_private.demo_batches where batch='showcase_20261017_v1';
 if owner is null or not exists(select 1 from public.core_profiles where id=owner) then
  raise exception 'Existing development demo owner required'; end if;
 for i in 1..12 loop
  people:=array_append(people,gen_random_uuid());
  insert into auth.users(id,aud,role,email,raw_app_meta_data,raw_user_meta_data,created_at,updated_at)
  values(people[i],'authenticated','authenticated',people[i]::text||'@example.test',
   jsonb_build_object('demo_batch',batch_name),jsonb_build_object('display_name','【示範】拼單夥伴'||i),ts,ts);
  insert into public.core_profiles(id,display_name,bio,badge)
  values(people[i],'【示範】拼單夥伴'||i,'無法登入的虛構展示人物，不會真實訂購或回覆。','🍱');
 end loop;
 insert into core_private.demo_batches(batch,owner_id,people) values(batch_name,owner,people);
 for i in 1..120 loop
  host:=people[1+(i%12)]; cap:=2+(i%5); mode:=i%12;
  deadline:=case when mode=2 then ts-interval '1 day'
   when i%7=0 then ts+interval '20 minutes'
   when i%7=1 then ts+interval '75 minutes'
   else ts+make_interval(hours=>hours[1+(i%6)]) end;
  accepted:=case when mode=0 then cap-1 else (i/12)%(cap-1) end;
  value:=jsonb_build_object('content','【示範】'||food[1+(i%8)]||'拼單 #'||i||
   '：測試名額、偏好與期限排序，沒有真實訂購或付款。',
   'tags',jsonb_build_array('開發示範',food[1+(i%8)],case when i%5=0 then '免費分享' else '一起拼單' end),
   'capacity',cap,'deadline_at',deadline,'price_per_person',case when i%5=0 then '0.00' else ((i%8)*15+45)::text end,
   'location',jsonb_build_object('name','【示範】'||places[1+(i%6)],'latitude',24.9876,
    'longitude',121.5760));
  insert into public.core_posts(author_id,payload,deadline_at,capacity,cancelled,request_key,
   original_payload,created_at,demo_batch,demo_owner_id)
  values(host,value,deadline,cap,mode=1,batch_name||'-post-'||i,value,
   ts-make_interval(mins=>(i%36)*20),batch_name,owner) returning id into post_id;
  for j in 1..accepted loop
   peer:=people[1+((i+j)%12)];
   insert into public.core_join_requests(post_id,requester_id,status,decided_at)
   values(post_id,peer,'accepted',ts);
  end loop;
  -- Both messages come from the synthetic host, always an authorized group member.
  insert into public.core_chat_messages(post_id,sender_id,content,client_key,created_at)
  values(post_id,host,'【示範訊息】這是虛構的取餐協調，請勿真的付款。',batch_name||'-msg-'||i||'-1',ts),
   (post_id,host,'【示範訊息】加入前請確認名額、費用及集合時間。',batch_name||'-msg-'||i||'-2',ts);
 end loop;
end $seed$;
