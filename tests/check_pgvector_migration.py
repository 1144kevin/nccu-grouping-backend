# ruff: noqa: E501
"""Isolated PostgreSQL/pgvector migration exercise, without network or user data."""

import subprocess
from pathlib import Path

CONTAINER = "nccu-vector-isolated-test"


def execute(sql):
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            CONTAINER,
            "psql",
            "-U",
            "postgres",
            "-d",
            "migration_test",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=sql,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(result.stderr)


def main():
    execute("""create schema extensions; create schema core_private;
do $$ begin if not exists(select 1 from pg_roles where rolname='anon') then create role anon; end if;
if not exists(select 1 from pg_roles where rolname='authenticated') then create role authenticated; end if; end $$;
create table public.core_stealth_signals(id uuid primary key,owner_id uuid,version int,payload jsonb,status text);
create table public.core_stealth_agents(owner_id uuid primary key,payload jsonb,last_run_at timestamptz);
create table public.core_stealth_proposals(fingerprint text,owners uuid[],signals uuid[],versions int[],policies int[],payload jsonb,created_at timestamptz);
create function core_private.account_guard() returns uuid language sql as $$ select '00000000-0000-0000-0000-000000000001'::uuid $$;
create function core_private.stealth_awake(jsonb,timestamptz) returns boolean language sql as $$ select true $$;""")
    execute(Path("supabase/core_semantic_discovery.sql").read_text(encoding="utf8"))
    execute("""insert into public.core_stealth_agents values('00000000-0000-0000-0000-000000000001','{}',null);
insert into public.core_stealth_signals values('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001',1,jsonb_build_object('expires_at', now()+interval '1 day'),'active');
do $$ declare v jsonb:=(select jsonb_agg(case when n=1 then 1 else 0 end) from generate_series(1,512)n); begin
perform public.core_semantic_discovery('index',jsonb_build_object('model_id','gemini:v1','items',jsonb_build_array(jsonb_build_object('signal_id','00000000-0000-0000-0000-000000000002','version',1,'vector',v)))); end $$;""")
    execute(Path("supabase/semantic_multi_model_staging.sql").read_text(encoding="utf8"))
    execute("""do $$ declare v jsonb:=(select jsonb_agg(case when n=2 then 1 else 0 end) from generate_series(1,512)n); begin
perform public.core_semantic_discovery('index',jsonb_build_object('model_id','openai:v1','items',jsonb_build_array(jsonb_build_object('signal_id','00000000-0000-0000-0000-000000000002','version',1,'vector',v))));
assert (select count(*) from public.core_signal_vectors)=2,'both models coexist';
assert (select count(*) from public.core_signal_vectors where model_id='gemini:v1')=1,'rollback vector retained';
update public.core_stealth_signals set version=2 where id='00000000-0000-0000-0000-000000000002';
assert (select count(*) from public.core_signal_vectors)=0,'changed signal invalidates both spaces';
perform public.core_semantic_discovery('index',jsonb_build_object('model_id','gemini:v1','items',jsonb_build_array(jsonb_build_object('signal_id','00000000-0000-0000-0000-000000000002','version',2,'vector',v))));
assert (select count(*) from public.core_signal_vectors where model_id='gemini:v1' and signal_version=2)=1,'old model can be rebuilt after change';
end $$;""")
    print("PASS real pgvector: dual index, invalidation and old-model rebuild")


if __name__ == "__main__":
    main()
