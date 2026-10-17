-- Additional recipient-owned demo memberships. Re-running never resets user choices.
insert into public.core_join_requests(post_id,requester_id,status,decided_at)
select p.id,p.demo_owner_id,case when p.payload->>'price_per_person'='0.00' then 'pending' else 'accepted' end,
  case when p.payload->>'price_per_person'='0.00' then null else now() end
from public.core_posts p
where p.demo_batch='showcase_20261017_v1' and p.author_id<>p.demo_owner_id
on conflict(post_id,requester_id) do nothing;
