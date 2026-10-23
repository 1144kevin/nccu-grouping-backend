// Local in-memory PostgreSQL test only. Install @electric-sql/pglite in .local/sql-validation.
import { PGlite } from '../.local/sql-validation/node_modules/@electric-sql/pglite/dist/index.js';
import { readFile } from 'node:fs/promises';
const root = new URL('../', import.meta.url);
const read = async (path) => readFile(new URL(path, root), 'utf8');
const pg = new PGlite();
try {
  const bootstrap = (await read('scripts/rehearse_postgres_restore.py')).match(/BOOTSTRAP = """([\s\S]*?)"""/)[1];
  await pg.exec('create role anon nologin; create role authenticated nologin;' + bootstrap);
  for (const name of ['core_schema.sql','core_validation_hardening.sql','core_chat.sql','core_chat_variable_scope.sql','core_chat_activity.sql','core_engagement.sql']) {
    await pg.exec(await read('supabase/' + name));
  }
  await pg.exec('alter table public.core_posts add column demo_batch text; alter table public.core_chat_messages add column moderation_hidden boolean not null default false;');
  const safety = await read('supabase/core_user_safety.sql');
  await pg.exec(safety.slice(0, safety.indexOf('create function core_private.block_users')));
  await pg.exec(await read('supabase/core_social_inbox.sql'));
  const result = await pg.exec(await read('supabase/core_social_inbox_smoke.sql'));
  console.log(JSON.stringify(result.at(-1).rows));
  console.log('In-memory PostgreSQL: membership, blocks, replay, inbox ownership, cursors and handoff consent passed.');
} catch (error) {
  console.error(error.message, error.detail ?? '', error.where ?? '');
  process.exitCode = 1;
} finally { await pg.close(); }
