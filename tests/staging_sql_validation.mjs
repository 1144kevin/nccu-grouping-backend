// Isolated in-memory PostgreSQL; no connection to any Supabase project.
import { PGlite } from '../.local/sql-validation/node_modules/@electric-sql/pglite/dist/index.js';
import { readFileSync } from 'node:fs';

const db = new PGlite();
await db.exec(readFileSync('supabase/openai_budget_staging.sql', 'utf8'));
await db.exec(`create role budget_untrusted;
do $$ begin
  assert not has_schema_privilege('budget_untrusted', 'ai_budget_private', 'USAGE'), 'private schema';
  assert not has_function_privilege('budget_untrusted', 'ai_budget_private.reserve(text,bigint)', 'EXECUTE'), 'private function';
end $$;`);
await db.exec(`do $$ begin
  assert ai_budget_private.reserve('text', 9999999), 'within daily cap';
  assert not ai_budget_private.reserve('text', 2), 'daily cap';
  assert ai_budget_private.reserve('text', 1), 'exact daily cap';
  assert not ai_budget_private.reserve('image', 1), 'past daily cap';
end $$;`);
await db.exec(`create table public.core_signal_vectors(signal_id uuid, model_id text, embedding int,
  primary key (signal_id));
  create function public.core_semantic_discovery(action text,args jsonb) returns jsonb
  language plpgsql as $$ begin
    insert into public.core_signal_vectors values ('00000000-0000-0000-0000-000000000001',args->>'model_id',1)
    on conflict(signal_id) do update set embedding=excluded.embedding;
    return '{}'; end $$;`);
await db.exec(readFileSync('supabase/semantic_multi_model_staging.sql', 'utf8'));
await db.exec(`select public.core_semantic_discovery('index','{"model_id":"gemini"}');
select public.core_semantic_discovery('index','{"model_id":"openai"}');
do $$ begin
 assert (select count(*) from public.core_signal_vectors)=2, 'two models coexist';
 assert (select count(*) from public.core_signal_vectors where model_id='gemini')=1, 'old model survives';
end $$;`);
console.log('PASS isolated SQL: budget daily boundary, model coexistence and rollback data');
await db.close();
