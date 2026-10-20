"""Synthetic application-graph backup only; restore targets session-local TEMP tables.

Not a whole Supabase dump: excludes Auth credentials, Storage objects and infrastructure.
No network or database access; SQL is prepared for reviewed MCP execution.
"""

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
TABLES = {"profiles": 3, "posts": 100, "joins": 32, "messages": 100}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def validate(graph):
    if set(graph) != {"scope", *TABLES} or not re.fullmatch(
        r"stealth-e2e-[0-9a-f]{32}", graph["scope"]
    ):
        raise ValueError("Only a bounded synthetic fixture graph is supported")
    for table, maximum in TABLES.items():
        if not isinstance(graph[table], list) or len(graph[table]) > maximum:
            raise ValueError("Graph exceeds safe rehearsal bounds")
        if any(not isinstance(row, dict) for row in graph[table]):
            raise ValueError("Invalid row shape")
    owners = {str(UUID(row["id"])) for row in graph["profiles"]}
    posts = {str(UUID(row["id"])) for row in graph["posts"]}
    if len(owners) != 3 or len(posts) != len(graph["posts"]) or not posts:
        raise ValueError("Three synthetic profiles and unique posts required")
    for row in graph["posts"]:
        if row["author_id"] not in owners or graph["scope"] not in row["payload"]["content"]:
            raise ValueError("Refusing unrelated post or owner")
    for name, owner_key in (("joins", "requester_id"), ("messages", "sender_id")):
        for row in graph[name]:
            if row["post_id"] not in posts or row[owner_key] not in owners:
                raise ValueError("Refusing unrelated graph relationship")
    if len(canonical(graph)) > 1024 * 1024:
        raise ValueError("Synthetic backup limited to 1 MiB")
    return graph


def pack(graph):
    validate(graph)
    return {
        "format": "synthetic-application-graph-v1",
        "data": graph,
        "sha256": hashlib.sha256(canonical(graph)).hexdigest(),
        "excludes": ["Auth credentials", "Storage objects", "database roles", "infrastructure"],
    }


def unpack(package):
    if package.get("format") != "synthetic-application-graph-v1":
        raise ValueError("Unsupported backup format")
    graph = validate(package["data"])
    if hashlib.sha256(canonical(graph)).hexdigest() != package.get("sha256"):
        raise ValueError("Backup checksum mismatch; restore refused")
    return graph


def restore_sql(package):
    graph = unpack(package)
    # Base64 contains no SQL quotes, delimiters or executable backup content.
    encoded = base64.b64encode(canonical(graph)).decode("ascii")
    return f"""begin;
set local lock_timeout='2s'; set local statement_timeout='15s';
create temporary table rehearsal_document(value jsonb);
insert into rehearsal_document values(convert_from(decode('{encoded}','base64'),'UTF8')::jsonb);
create temporary table restored_profiles(like public.core_profiles including all);
create temporary table restored_posts(like public.core_posts including all);
create temporary table restored_joins(like public.core_join_requests including all);
create temporary table restored_messages(like public.core_chat_messages including all);
alter table restored_posts add foreign key(author_id) references restored_profiles(id);
alter table restored_posts add foreign key(demo_owner_id) references restored_profiles(id);
alter table restored_joins add foreign key(post_id) references restored_posts(id);
alter table restored_joins add foreign key(requester_id) references restored_profiles(id);
alter table restored_messages add foreign key(post_id) references restored_posts(id);
alter table restored_messages add foreign key(sender_id) references restored_profiles(id);
insert into restored_profiles select x.* from rehearsal_document d cross join lateral
  jsonb_populate_recordset(null::public.core_profiles,d.value->'profiles') x;
insert into restored_posts select x.* from rehearsal_document d cross join lateral
  jsonb_populate_recordset(null::public.core_posts,d.value->'posts') x;
insert into restored_joins select x.* from rehearsal_document d cross join lateral
  jsonb_populate_recordset(null::public.core_join_requests,d.value->'joins') x;
insert into restored_messages select x.* from rehearsal_document d cross join lateral
  jsonb_populate_recordset(null::public.core_chat_messages,d.value->'messages') x;
do $check$
declare original jsonb; restored jsonb;
begin
 select value into original from rehearsal_document;
 select jsonb_build_object('scope',original->'scope',
  'profiles',(select coalesce(jsonb_agg(to_jsonb(x) order by id),'[]') from restored_profiles x),
  'posts',(select coalesce(jsonb_agg(to_jsonb(x) order by id),'[]') from restored_posts x),
  'joins',(select coalesce(jsonb_agg(to_jsonb(x) order by id),'[]') from restored_joins x),
  'messages',(select coalesce(jsonb_agg(to_jsonb(x) order by id),'[]') from restored_messages x))
  into restored;
 assert restored=original,'Restored rows differ from backup';
 begin
   insert into restored_messages select (jsonb_populate_record(null::public.core_chat_messages,
     (original->'messages'->0)||jsonb_build_object('id',gen_random_uuid(),'post_id',gen_random_uuid(),
       'client_key','missing-parent-test'))).*;
   raise exception 'Missing parent accepted';
 exception when foreign_key_violation then null; end;
end $check$;
select jsonb_build_object('status','restored_exactly','target','session-local TEMP tables',
 'profiles',(select count(*) from restored_profiles),'posts',(select count(*) from restored_posts),
 'joins',(select count(*) from restored_joins),'messages',(select count(*) from restored_messages),
 'foreign_keys_verified',true) as restore_result;
rollback;
"""


def private_path(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT / ".local"):
        raise ValueError("Rehearsal artifacts must stay in this project's ignored .local")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("pack", "restore-sql"))
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    options = parser.parse_args()
    source, output = private_path(options.source), private_path(options.output)
    if source.stat().st_size > 1024 * 1024:
        raise SystemExit("Rehearsal file too large")
    value = json.loads(source.read_text(encoding="utf8"))
    result = (
        json.dumps(pack(value), ensure_ascii=False, indent=2)
        if options.action == "pack"
        else restore_sql(value)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result, encoding="utf8")
    print(f"Prepared {options.action}; synthetic data only, no database/network operation.")
