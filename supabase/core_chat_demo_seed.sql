-- Messages are visibly synthetic and only inserted into the authorized demo batch.
insert into public.core_chat_messages(post_id,sender_id,content,client_key,created_at)
select p.id,p.author_id,'【示範訊息】大家好，取餐時間先訂六點，集合點在政大校門口。',
  'showcase_chat_host_'||p.id::text,clock_timestamp()-interval '5 minutes'
from public.core_posts p where p.demo_batch='showcase_20261017_v1'
  and p.payload->>'content' like '%便當晚餐%'
on conflict(sender_id,client_key) do nothing;

insert into public.core_chat_messages(post_id,sender_id,content,client_key,created_at)
select p.id,j.requester_id,'【示範訊息】收到，我會準時到；這段是測試對話，不是真的訂餐。',
  'showcase_chat_member_'||p.id::text,clock_timestamp()-interval '4 minutes'
from public.core_posts p join public.core_join_requests j on j.post_id=p.id
join core_private.demo_batches b on b.batch=p.demo_batch
where p.demo_batch='showcase_20261017_v1' and p.payload->>'content' like '%便當晚餐%'
  and j.requester_id=b.people[1] and j.status='accepted'
on conflict(sender_id,client_key) do nothing;

insert into public.core_chat_messages(post_id,sender_id,content,client_key,created_at)
select p.id,p.author_id,'【示範訊息】歡迎加入咖啡拼單，口味與取餐時間可以在這裡協調。示範人物不會自動回覆。',
  'showcase_chat_coffee_'||p.id::text,clock_timestamp()-interval '2 minutes'
from public.core_posts p where p.demo_batch='showcase_20261017_v1'
  and p.payload->>'content' like '%咖啡外帶%'
on conflict(sender_id,client_key) do nothing;
