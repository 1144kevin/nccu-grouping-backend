// Disposable PostgreSQL, synthetic participants only; no network or persistent writes.
import { PGlite } from '../.local/sql-validation/node_modules/@electric-sql/pglite/dist/index.js';
import { readFileSync } from 'node:fs';
const db = new PGlite();
const config = JSON.parse(readFileSync(new URL('./stealth_flow_bootstrap.json', import.meta.url), 'utf8'));
await db.exec(config.bootstrap);
for (const file of config.migrations) {
  console.log('Apply', file);
  await db.exec(readFileSync(`supabase/${file}`, 'utf8'));
}
const safety = readFileSync('supabase/core_user_safety.sql', 'utf8');
await db.exec(safety.slice(0, safety.indexOf('create function core_private.group_send_allowed')));
await db.exec(readFileSync('supabase/core_stealth_quick.sql', 'utf8'));
await db.exec(readFileSync('supabase/core_stealth_room_continuity.sql', 'utf8').replaceAll('core_private.stealth_quick_before_inbox', 'core_private.stealth_quick'));
const chatTerms = readFileSync('supabase/core_stealth_chat_terms.sql', 'utf8');
await db.exec(chatTerms.slice(0, chatTerms.indexOf(" d:=pg_get_functiondef('core_private.stealth_quick")) + 'end $migration$;');
await db.exec(readFileSync('supabase/core_stealth_shared_items_location.sql', 'utf8'));
console.log('Baseline prepared');
await db.exec(readFileSync('supabase/core_stealth_quick_smoke.sql', 'utf8'));
console.log('PASS isolated multi-account matching, chat, consent, blocking and expiry');
await db.exec(readFileSync('supabase/core_stealth_shared_items_location_smoke.sql', 'utf8'));
console.log('PASS chocolate reasons, no invented terms, revised location/price, reset/stale consent, two-account formation and final coordinates');
await db.close();
