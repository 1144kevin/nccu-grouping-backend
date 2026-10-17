-- Cover the new ownership foreign keys and the account-scoped cleanup lookup.
create index core_demo_batches_owner on core_private.demo_batches(owner_id);
create index core_posts_demo_owner on public.core_posts(demo_owner_id,demo_batch,id);
