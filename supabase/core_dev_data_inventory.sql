-- Read-only inventory. Demo identities are counted once, not again as profiles.
-- Run only against the explicitly selected development project.
select 'posts' as kind, count(*) as total,
 count(*) filter(where demo_batch is not null) as marked_demo from public.core_posts
union all select 'profiles',count(*),
 count(*) filter(where id in(select id from auth.users where raw_app_meta_data ? 'demo_batch'))
 from public.core_profiles
union all select 'chat_messages',count(*),
 count(*) filter(where post_id in(select id from public.core_posts where demo_batch is not null))
 from public.core_chat_messages
union all select 'join_requests',count(*),
 count(*) filter(where post_id in(select id from public.core_posts where demo_batch is not null))
 from public.core_join_requests
union all select 'stealth_agents',count(*),
 count(*) filter(where owner_id in(select id from auth.users where raw_app_meta_data ? 'demo_batch'))
 from public.core_stealth_agents
union all select 'stealth_signals',count(*),
 count(*) filter(where owner_id in(select id from auth.users where raw_app_meta_data ? 'demo_batch'))
 from public.core_stealth_signals
union all select 'stealth_proposals',count(*),
 count(*) filter(where coalesce((payload->>'is_demo')::boolean,false))
 from public.core_stealth_proposals
union all select 'isolated_e2e_accounts',count(*),0 from auth.users
 where raw_app_meta_data->>'stealth_test_scope' like 'stealth-e2e-%';

select count(*) filter(where not cancelled and deadline_at>now()) as ongoing_posts,
 count(*) filter(where cancelled) as cancelled_posts,
 count(*) filter(where not cancelled and deadline_at<=now()) as expired_posts
from public.core_posts;
