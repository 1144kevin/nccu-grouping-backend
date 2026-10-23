-- Development-only: add fixture applications to existing active posts; never create posts.
-- Re-running preserves every existing acceptance/rejection and inbox preference.
do $seed$
declare recipient record; target record; owner uuid; batch_name text; people uuid[]; i int; thread_id uuid;
 names text[]:=array['小安','品妤','阿哲']; badges text[]:=array['🥐','🍜','🍍'];
begin
 for recipient in select id from public.core_profiles where display_name in ('梁測試','祥 測試') loop
  owner:=recipient.id; batch_name:='showcase_existing_applications_20261023_'||owner::text;
  perform pg_advisory_xact_lock(hashtext(batch_name));
  select b.people into people from core_private.demo_batches b where batch=batch_name;
  if not found then
   people:=array[gen_random_uuid(),gen_random_uuid(),gen_random_uuid()];
   for i in 1..3 loop
    insert into auth.users(id,aud,role,email,raw_app_meta_data,raw_user_meta_data,created_at,updated_at)
     values(people[i],'authenticated','authenticated',people[i]::text||'@example.test',jsonb_build_object('demo_batch',batch_name),jsonb_build_object('display_name',names[i]),now(),now());
    insert into public.core_profiles(id,display_name,bio,badge) values(people[i],names[i],'開發情境人物，沒有登入憑證，不是真實訂購。',badges[i]);
   end loop;
   insert into core_private.demo_batches(batch,owner_id,people) values(batch_name,owner,people);
  end if;
  for target in select p.* from public.core_posts p where author_id=owner and demo_batch is null and not cancelled and deadline_at>clock_timestamp()
   order by created_at desc limit 3 loop
   for i in 1..3 loop
    insert into public.core_join_requests(post_id,requester_id,status,created_at) values(target.id,people[i],'pending',clock_timestamp()-i*interval '2 minutes')
     on conflict(post_id,requester_id) do nothing;
    insert into public.core_direct_threads(post_id,host_id,guest_id) values(target.id,owner,people[i]) on conflict(post_id,guest_id) do nothing;
    select id into thread_id from public.core_direct_threads where post_id=target.id and guest_id=people[i];
    insert into public.core_direct_messages(thread_id,sender_id,content,client_key) values(thread_id,people[i],
     case i when 1 then '請問還有名額嗎？我可以配合取餐時間。' when 2 then '想一起加入，請問在哪裡碰面？' else '我有送出申請了，再麻煩確認，謝謝！' end,
     'example-inquiry-'||target.id::text||'_'||i) on conflict(sender_id,client_key) do nothing;
   end loop;
  end loop;
 end loop;
end $seed$;
-- Retire only the host examples created by the previous fixtures; retain all history/messages.
update public.core_posts set cancelled=true,version=version+1
 where author_id=demo_owner_id and not cancelled and (demo_batch like 'showcase_inbox_20261023_v2_%' or demo_batch='showcase_20261023_applications_v1');
