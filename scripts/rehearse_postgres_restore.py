"""Opt-in local, networkless PostgreSQL dump/restore using fresh synthetic data only.

No database URL, cloud credentials, host ports, persistent volumes or arbitrary restore input.
This covers the checked-in core/chat baseline, not a full Supabase service recovery.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from uuid import UUID, uuid4

from scripts.rehearse_container import ROOT, local_endpoint

IMAGE = "postgres:17-bookworm"
MIGRATIONS = (
    "core_schema.sql",
    "core_validation_hardening.sql",
    "core_chat.sql",
    "core_chat_variable_scope.sql",
)
ROLES = "create role anon nologin; create role authenticated nologin;"
BOOTSTRAP = """
create schema auth;
create table auth.users(id uuid primary key,email text,raw_user_meta_data jsonb default '{}',
 email_confirmed_at timestamptz,is_anonymous boolean default false);
create table auth.sessions(id uuid primary key,user_id uuid references auth.users(id),
 not_after timestamptz);
create function auth.jwt() returns jsonb language sql stable as $$
 select coalesce(nullif(current_setting('request.jwt.claims',true),''),'{}')::jsonb $$;
create function auth.uid() returns uuid language sql stable as $$
 select (auth.jwt()->>'sub')::uuid $$;
grant usage on schema auth to anon,authenticated;
grant execute on function auth.jwt(),auth.uid() to anon,authenticated;
"""


def local_docker():
    cli = shutil.which("docker")
    if not cli:
        raise RuntimeError("Docker CLI required")
    docker = [cli, "--context", "default"]
    endpoint = command(
        docker + ["context", "inspect", "default", "--format", "{{.Endpoints.docker.Host}}"]
    )
    if not local_endpoint(endpoint.strip()):
        raise ValueError("Only a local Docker engine is allowed")
    if command(docker + ["info", "--format", "{{.OSType}}"]).strip() != "linux":
        raise ValueError("Local Linux engine required")
    return docker


def command(args, data=None, timeout=120, allow_failure=False, binary=False):
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("DOCKER_HOST", "DOCKER_CONTEXT", "BUILDX_BUILDER", "BUILDKIT_HOST")
    }
    result = subprocess.run(
        args, input=data, capture_output=True, cwd=ROOT, env=env, timeout=timeout
    )
    if result.returncode and not allow_failure:
        target = ROOT / ".local" / "postgres-rehearsal-failure.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(result.stderr)
        raise RuntimeError("Local command failed; inspect ignored postgres-rehearsal-failure.log")
    return result if allow_failure else (result.stdout if binary else result.stdout.decode("utf8"))


def sql(docker, name, statement, allow_failure=False):
    return command(
        docker
        + [
            "exec",
            "-i",
            name,
            "psql",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            "postgres",
        ],
        statement.encode("utf8"),
        allow_failure=allow_failure,
    )


def postgres_flags(name):
    return [
        "run",
        "--detach",
        "--name",
        name,
        "--network",
        "none",
        "--read-only",
        "--user",
        "999:999",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--pids-limit",
        "128",
        "--tmpfs",
        "/var/lib/postgresql/data:rw,nosuid,size=256m,uid=999,gid=999",
        "--tmpfs",
        "/var/run/postgresql:rw,noexec,nosuid,size=16m,uid=999,gid=999",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=32m",
        "--env",
        "POSTGRES_HOST_AUTH_METHOD=trust",
        IMAGE,
        "-c",
        "listen_addresses=",
    ]


def start(docker, name):
    command(docker + postgres_flags(name))
    for _ in range(45):
        result = command(
            docker + ["exec", name, "pg_isready", "-U", "postgres"], allow_failure=True
        )
        if result.returncode == 0:
            # initdb's temporary server also becomes ready; wait for the final PID1 server.
            pid = command(docker + ["exec", name, "cat", "/proc/1/comm"]).strip()
            if pid == "postgres":
                return
        time.sleep(1)
    raise RuntimeError("Local PostgreSQL startup deadline exceeded")


def fixture_sql():
    owners = [str(UUID(int=i)) for i in range(1, 4)]
    posts = [str(UUID(int=i)) for i in range(100, 125)]
    statements = []
    for i, owner in enumerate(owners, 1):
        statements += [
            f"insert into auth.users(id,email) values('{owner}','fixture{i}@example.invalid');",
            f"insert into auth.sessions values('{owner}','{owner}',now()+interval '1 day');",
            f"insert into core_profiles(id,display_name) values('{owner}','Fixture {i}');",
        ]
    for i, post in enumerate(posts):
        payload = json.dumps({"content": f"Synthetic backup meal grouping {i}", "tags": ["meal"]})
        statements.append(
            f"insert into core_posts(id,author_id,payload,original_payload,deadline_at,"
            f"capacity,request_key) values('{post}','{owners[0]}','{payload}',"
            f"'{payload}',now()+interval '1 day',3,'backup-fixture-{i:04}');"
        )
    statements += [
        f"insert into core_join_requests(post_id,requester_id,status) values('{posts[0]}',"
        f"'{owner}','accepted');"
        for owner in owners[1:]
    ]
    statements += [
        f"insert into core_chat_messages(post_id,sender_id,content,client_key) values("
        f"'{posts[0]}','{owners[1]}','Synthetic restore chat','backup-chat-fixture');",
        "create schema rehearsal_meta; create sequence rehearsal_meta.event_id;",
        "create table rehearsal_meta.events(id bigint primary key default "
        "nextval('rehearsal_meta.event_id'),post_id uuid references core_posts(id));",
        "create function rehearsal_meta.track() returns trigger language plpgsql as $$ "
        "begin insert into rehearsal_meta.events(post_id) values(new.id); return new; end $$;",
        "create trigger rehearsal_audit after update on core_posts for each row "
        "execute function rehearsal_meta.track();",
        f"update core_posts set version=version+1 where id='{posts[0]}';",
    ]
    return "\n".join(statements)


def snapshot(docker, name):
    tables = (
        "auth.users",
        "auth.sessions",
        "public.core_profiles",
        "public.core_posts",
        "public.core_join_requests",
        "public.core_chat_messages",
        "rehearsal_meta.events",
    )
    rows = {
        table: json.loads(
            sql(
                docker,
                name,
                f"select coalesce(jsonb_agg(to_jsonb(t) order by id),'[]') from {table} t;",
            )
        )
        for table in tables
    }
    catalog = sql(
        docker,
        name,
        """
select jsonb_build_object(
 'tables',(select jsonb_agg(jsonb_build_array(n.nspname,c.relname,c.relrowsecurity,
  array(select a::text from unnest(coalesce(c.relacl,
   acldefault((case when c.relkind='S' then 'S' else 'r' end)::"char",c.relowner))) a
   order by a::text))
  order by n.nspname,c.relname) from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where n.nspname in ('public','auth','core_private','rehearsal_meta') and c.relkind in ('r','S')),
 'policies',(select jsonb_agg(to_jsonb(p) order by schemaname,tablename,policyname)
  from pg_policies p),
 'constraints',(select jsonb_agg(pg_get_constraintdef(oid)
  order by conname,pg_get_constraintdef(oid))
  from pg_constraint where connamespace in (select oid from pg_namespace where nspname in
  ('auth','public','core_private','rehearsal_meta'))),
 'indexes',(select jsonb_agg(indexdef order by schemaname,indexname)
  from pg_indexes where schemaname
  in ('auth','public','core_private','rehearsal_meta')),
 'functions',(select jsonb_agg(jsonb_build_array(pg_get_functiondef(p.oid),p.proacl::text)
  order by n.nspname,p.proname,pg_get_function_identity_arguments(p.oid)) from pg_proc p join
  pg_namespace n on n.oid=p.pronamespace
  where n.nspname in ('auth','public','core_private','rehearsal_meta')),
 'triggers',(select jsonb_agg(pg_get_triggerdef(oid) order by tgname)
  from pg_trigger where not tgisinternal),
 'sequence',(select jsonb_build_array(last_value,is_called) from rehearsal_meta.event_id),
 'roles',(select jsonb_agg(jsonb_build_array(rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,
  rolcanlogin,rolbypassrls) order by rolname)
  from pg_roles where rolname in ('anon','authenticated')));
""",
    )
    return {"rows": rows, "catalog": json.loads(catalog)}


def checked_archive(path, checksum):
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Archive checksum/size mismatch; restore refused")
    data = path.read_bytes()
    if len(data) > 16 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError("Archive checksum/size mismatch; restore refused")
    return data


def require_blank(docker, name):
    if (
        sql(
            docker,
            name,
            "select (select count(*) from pg_namespace where nspname not in "
            "('pg_catalog','information_schema','public') and nspname not like 'pg_%') + "
            "(select count(*) from pg_class where relnamespace='public'::regnamespace) + "
            "(select count(*) from pg_proc where pronamespace='public'::regnamespace);",
        ).strip()
        != "0"
    ):
        raise ValueError("Restore target must be fresh and empty")


def run():
    docker = local_docker()
    suffix = uuid4().hex[:12]
    source, target = ("nccu-backup-source-" + suffix, "nccu-backup-target-" + suffix)
    directory = ROOT / ".local" / "postgres-backups" / suffix
    directory.mkdir(parents=True)
    report = {"status": "failed", "supabase_accessed": False, "network": "none"}
    source_removed = False
    try:
        command(docker + ["pull", IMAGE], timeout=300)
        report["postgres_image"] = json.loads(command(docker + ["image", "inspect", IMAGE]))[0][
            "RepoDigests"
        ][0]
        start(docker, source)
        sql(docker, source, ROLES + BOOTSTRAP)
        for migration in MIGRATIONS:
            sql(docker, source, (ROOT / "supabase" / migration).read_text(encoding="utf8"))
        sql(docker, source, fixture_sql())
        before = snapshot(docker, source)
        dump = command(
            docker
            + ["exec", source, "pg_dump", "-U", "postgres", "-d", "postgres", "--format=custom"],
            binary=True,
        )
        archive = directory / "application.dump"
        archive.write_bytes(dump)
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        report.update(
            sha256=checksum,
            archive_bytes=archive.stat().st_size,
            counts={k: len(v) for k, v in before["rows"].items()},
            migrations=list(MIGRATIONS),
        )
        (directory / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf8")
        # Prove the target does not rely on the source server still being available.
        command(docker + ["rm", "-f", source])
        source_removed = True
        start(docker, target)
        require_blank(docker, target)
        restore = docker + [
            "exec",
            "-i",
            target,
            "pg_restore",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "--exit-on-error",
            "--single-transaction",
        ]
        data = checked_archive(archive, checksum)
        missing_role = command(restore, data, allow_failure=True)
        if not missing_role.returncode:
            raise RuntimeError("Restore unexpectedly succeeded without required roles")
        require_blank(docker, target)
        report["missing_role_rolled_back"] = True
        sql(docker, target, ROLES)
        command(restore, data)
        after = snapshot(docker, target)
        if after != before:
            (directory / "comparison.json").write_text(
                json.dumps({"before": before, "after": after}, indent=2), encoding="utf8"
            )
            raise RuntimeError("Restored rows/schema/permissions differ from source")
        try:
            require_blank(docker, target)
        except ValueError:
            report["nonempty_restore_refused"] = True
        else:
            raise RuntimeError("Nonempty restore guard failed")
        corrupt = directory / "tampered.dump"
        corrupt.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        try:
            checked_archive(corrupt, checksum)
        except ValueError:
            report["corrupt_archive_refused"] = True
        else:
            raise RuntimeError("Corrupt archive guard failed")
        corrupt.unlink()
        # Exercise actual restored RPC and RLS with a synthetic session and restricted role.
        owner = str(UUID(int=1))
        claims = json.dumps({"sub": owner, "session_id": owner})
        result = sql(
            docker,
            target,
            f"begin; set local role authenticated; select set_config("
            f"'request.jwt.claims','{claims}',true); "
            "select public.core_dispatch('me','{}'); rollback;",
        )
        if "Fixture 1" not in result:
            raise RuntimeError("Restored authenticated RPC failed")
        chat = sql(
            docker,
            target,
            f"begin; set local role authenticated; select set_config("
            f"'request.jwt.claims','{claims}',true); select public.core_chat('messages',"
            '\'{"post_id":"00000000-0000-0000-0000-000000000064"}\'); rollback;',
        )
        if "Synthetic restore chat" not in chat:
            raise RuntimeError("Restored chat RPC failed")
        for role, statement in (
            ("anon", "select * from core_profiles"),
            ("authenticated", "update core_posts set capacity=20"),
        ):
            denied = sql(docker, target, f"set role {role}; {statement};", allow_failure=True)
            if denied.returncode == 0 or b"permission denied" not in denied.stderr:
                raise RuntimeError("Restored privilege boundary failed")
        sql(
            docker,
            target,
            """do $$ begin
 begin update core_posts set capacity=1; raise exception 'check accepted';
 exception when check_violation then null; end;
 begin insert into core_join_requests(post_id,requester_id,status) values(gen_random_uuid(),
 '00000000-0000-0000-0000-000000000001','pending'); raise exception 'orphan accepted';
 exception when foreign_key_violation then null; end;
 end $$;
 begin; update core_posts set version=version+1;
 do $$ begin assert (select count(*) from rehearsal_meta.events)=26; end $$; rollback;
""",
        )
        report.update(
            status="passed",
            rows_and_catalog_equal=True,
            rls_rpc_verified=True,
            chat_rpc_verified=True,
            constraints_and_trigger_verified=True,
            source_removed_before_restore=True,
        )
    finally:
        names = [target] if source_removed else [source, target]
        cleanup = [
            command(docker + ["rm", "-f", name], allow_failure=True).returncode == 0
            for name in names
        ]
        report["containers_removed"] = all(cleanup)
        (directory / "result.json").write_text(json.dumps(report, indent=2), encoding="utf8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", action="store_true", help="Create disposable local containers and test"
    )
    if parser.parse_args().run:
        print(json.dumps(run(), indent=2))
    else:
        print("Check-only: --run performs a synthetic dump/restore on disposable local containers.")
