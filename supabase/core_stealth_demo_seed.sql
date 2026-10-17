-- Reuse only the existing inert showcase people. Never enables a human's agent.
begin;
insert into public.core_stealth_agents(owner_id,payload)
select p.id,jsonb_build_object('name','【示範】偷揪分身','enabled',true,'include_demo',true,'daily_invite_limit',10,
  'timezone','Asia/Taipei','quiet_start_hour',null,'quiet_end_hour',null,'exploration','balanced','tone','warm','avatar_key','rice')
from public.core_profiles p join auth.users u on u.id=p.id
where u.raw_app_meta_data->>'demo_batch'='showcase_20261017_v1'
  and p.display_name in ('【示範】小林','【示範】小陳','【示範】小黃')
  and coalesce(u.encrypted_password,'')='' and not exists(select 1 from auth.identities i where i.user_id=u.id)
on conflict(owner_id) do nothing;
insert into public.core_stealth_signals(owner_id,payload,request_key)
select a.owner_id,jsonb_build_object('kind',case p.display_name when '【示範】小林' then 'need' when '【示範】小陳' then 'offer' else 'plan' end,
  'category','meal','acceptable_categories','[]'::jsonb,
  'title',case p.display_name when '【示範】小林' then '【示範配對】晚餐一起分食' when '【示範】小陳' then '【示範配對】提供正餐優惠資源並願意下單' else '【示範配對】經過正門並願意順路取餐' end,
  'location_id','main_gate','starts_at',now()+interval '5 minutes','ends_at',now()+interval '23 hours','expires_at',now()+interval '23 hours',
  'budget_max_twd',120,'known_cost_twd',90,'tags',jsonb_build_array('晚餐'),'dietary_requirements','[]'::jsonb,'dietary_verified','[]'::jsonb,
  'roles',case p.display_name when '【示範】小林' then jsonb_build_array('share') when '【示範】小陳' then jsonb_build_array('coupon','order') else jsonb_build_array('pickup') end),a.owner_id
from public.core_stealth_agents a join public.core_profiles p on p.id=a.owner_id join auth.users u on u.id=a.owner_id
where u.raw_app_meta_data->>'demo_batch'='showcase_20261017_v1' and p.display_name in ('【示範】小林','【示範】小陳','【示範】小黃')
  and coalesce(u.encrypted_password,'')='' and not exists(select 1 from auth.identities i where i.user_id=u.id)
on conflict(owner_id,request_key) do nothing;
commit;
select count(*) as demo_signals from public.core_stealth_signals s join auth.users u on u.id=s.owner_id where u.raw_app_meta_data->>'demo_batch'='showcase_20261017_v1';
