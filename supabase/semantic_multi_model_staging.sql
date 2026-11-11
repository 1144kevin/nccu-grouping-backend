-- STAGING ONLY. Back up and verify the live function and index definitions before any deployment.
-- Keeps both old and new embedding spaces until an explicitly approved cutover.
begin;
lock table public.core_signal_vectors in access exclusive mode;
alter table public.core_signal_vectors drop constraint core_signal_vectors_pkey;
alter table public.core_signal_vectors add primary key (signal_id, model_id);
do $$
declare
  source text := pg_get_functiondef('public.core_semantic_discovery(text,jsonb)'::regprocedure);
  patched text;
begin
  patched := replace(source, 'on conflict(signal_id) do update set',
                    'on conflict(signal_id,model_id) do update set');
  if patched = source then
    raise exception 'unexpected discovery function; migration aborted';
  end if;
  execute patched;
end $$;
commit;
