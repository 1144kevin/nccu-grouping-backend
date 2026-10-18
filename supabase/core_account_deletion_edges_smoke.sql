-- Isolated synthetic users only; every change is rolled back, including Auth rows.
begin;
create temporary table deletion_edges(name text,user_id uuid,session_id uuid,alternate_session uuid default gen_random_uuid());
insert into deletion_edges values('delete',gen_random_uuid(),gen_random_uuid()),('keep',gen_random_uuid(),gen_random_uuid());
insert into auth.users(id,email,email_confirmed_at) select user_id,user_id::text||'@example.test',now() from deletion_edges;
insert into auth.sessions(id,user_id,created_at) select session_id,user_id,clock_timestamp() from deletion_edges;
insert into auth.sessions(id,user_id,created_at) select alternate_session,user_id,clock_timestamp() from deletion_edges;
create temporary table deletion_edge_preview(value jsonb);
grant select on deletion_edge_preview to authenticated;
grant select on deletion_edges to authenticated;
do $$ declare f record; begin
 for f in select * from deletion_edges loop
  perform set_config('request.jwt.claims',jsonb_build_object('sub',f.user_id,'session_id',f.session_id,'role','authenticated')::text,true);
  perform public.core_dispatch('me');
 end loop;
end $$;
-- Seed FK-heavy historical state without contacting a model or creating a real order.
insert into public.core_stealth_agents(owner_id,payload) select user_id,'{}' from deletion_edges;
insert into public.core_stealth_signals(owner_id,payload,request_key) select user_id,'{}',gen_random_uuid() from deletion_edges;
insert into public.core_user_blocks(owner_id,blocked_id)
 select a.user_id,b.user_id from deletion_edges a cross join deletion_edges b where a.name='delete' and b.name='keep';
do $$ declare a record; b record; p jsonb; v jsonb; begin
 select * into a from deletion_edges where name='delete'; select * into b from deletion_edges where name='keep';
 perform set_config('request.jwt.claims',jsonb_build_object('sub',b.user_id,'session_id',b.session_id,'role','authenticated')::text,true);
 v:=jsonb_build_object('content',E'合成成團資料\n下單：待刪成員；取餐：保留成員\n配合時段：測試','tags',jsonb_build_array('驗證'),'capacity',2,'deadline_at',clock_timestamp()+interval '2 hours','price_per_person',null,'location',jsonb_build_object('name','合成點','latitude',24.987,'longitude',121.576));
 p:=public.core_dispatch('create',jsonb_build_object('request_key','deletion-edge-keep','value',v));
 insert into public.core_stealth_proposals(fingerprint,owners,signals,signal_versions,policy_versions,payload,terms_author_id,formed_post_id)
 values(gen_random_uuid()::text,array[a.user_id,b.user_id],array(select id from public.core_stealth_signals where owner_id in(a.user_id,b.user_id)),array[1,1],array[1,1],'{}',a.user_id,(p->>'id')::uuid);
 assert core_private.stealth_candidate(array(select id from public.core_stealth_signals where owner_id in(a.user_id,b.user_id)),clock_timestamp()) is null;
 p:=public.core_dispatch('create',jsonb_build_object('request_key','deletion-edge-demo','value',v));
 insert into core_private.demo_batches(batch,owner_id,people) values('deletion-edge',a.user_id,array[b.user_id]);
 update public.core_posts set demo_batch='deletion-edge',demo_owner_id=a.user_id where id=(p->>'id')::uuid;
end $$;
update auth.sessions set created_at=clock_timestamp()-interval '16 minutes' where user_id=(select user_id from deletion_edges where name='delete');
set local role authenticated;
do $$ declare a record; begin
 select * into a from deletion_edges where name='delete';
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a.user_id,'session_id',a.session_id,'role','authenticated')::text,true);
 assert not (public.core_account_deletion('status')->>'recent_auth')::boolean;
 begin perform public.core_account_deletion('prepare'); raise exception 'old session accepted'; exception when sqlstate 'PT403' then null; end;
end $$;
reset role;
update auth.sessions set created_at=clock_timestamp() where user_id=(select user_id from deletion_edges where name='delete');
insert into deletion_edge_preview values(public.core_account_deletion('prepare'));
update core_private.account_deletion_jobs set expires_at=clock_timestamp()-interval '1 second' where owner_id=(select user_id from deletion_edges where name='delete');
set local role authenticated;
do $$ declare preview jsonb; a record; begin
 select * into a from deletion_edges where name='delete';
 select value into preview from deletion_edge_preview;
 begin perform public.core_account_deletion('begin',jsonb_build_object('confirmation','DELETE','challenge',preview->>'challenge')); raise exception 'expired nonce accepted'; exception when sqlstate 'PT409' then null; end;
 preview:=public.core_account_deletion('prepare');
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a.user_id,'session_id',a.alternate_session,'role','authenticated')::text,true);
 begin perform public.core_account_deletion('begin',jsonb_build_object('confirmation','DELETE','challenge',preview->>'challenge')); raise exception 'nonce from another session accepted'; exception when sqlstate 'PT409' then null; end;
 perform set_config('request.jwt.claims',jsonb_build_object('sub',a.user_id,'session_id',a.session_id,'role','authenticated')::text,true);
 perform public.core_account_deletion('begin',jsonb_build_object('confirmation','DELETE','challenge',preview->>'challenge'));
 assert public.core_account_deletion('finish')->>'deleted'='true';
end $$;
reset role;
do $$ declare a uuid; b uuid; begin
 select user_id into a from deletion_edges where name='delete'; select user_id into b from deletion_edges where name='keep';
 assert not exists(select 1 from auth.users where id=a);
 assert exists(select 1 from auth.users where id=b);
 assert not exists(select 1 from public.core_stealth_proposals where a=any(owners));
 assert not exists(select 1 from public.core_stealth_agents where owner_id=a);
 assert exists(select 1 from public.core_stealth_signals where owner_id=b);
 assert not exists(select 1 from public.core_user_blocks where owner_id=a or blocked_id=a);
 assert not exists(select 1 from core_private.demo_batches where owner_id=a);
 assert not exists(select 1 from public.core_posts where demo_owner_id=a);
 assert exists(select 1 from public.core_posts where author_id=b and payload->>'content' like '%成員帳號已刪除%');
end $$;
rollback;
