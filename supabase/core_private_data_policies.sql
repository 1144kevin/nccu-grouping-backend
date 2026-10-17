-- Make the intended denial explicit, including if a table grant is added later.
-- Application access remains through the session-checked RPCs only.
create policy core_chat_messages_no_direct_access on public.core_chat_messages
  for all to anon,authenticated using (false) with check (false);
create policy core_chat_reads_no_direct_access on public.core_chat_reads
  for all to anon,authenticated using (false) with check (false);
create policy core_demo_manifest_no_direct_access on core_private.demo_batches
  for all to anon,authenticated using (false) with check (false);
