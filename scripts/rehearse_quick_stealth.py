"""Networkless disposable PostgreSQL validation; no cloud data or credentials."""

import json
from uuid import uuid4

from scripts.rehearse_postgres_restore import (
    BOOTSTRAP,
    ROLES,
    ROOT,
    command,
    local_docker,
    sql,
    start,
)

MIGRATIONS = (
    "core_schema.sql",
    "core_validation_hardening.sql",
    "core_chat.sql",
    "core_chat_variable_scope.sql",
    "core_stealth_cloud.sql",
    "core_stealth_cloud_scope.sql",
    "core_stealth_cloud_demo_optin.sql",
    "core_stealth_cloud_rank.sql",
    "core_stealth_test_isolation.sql",
    "core_stealth_formation.sql",
    "core_stealth_formation_location_note.sql",
    "core_stealth_planning_context.sql",
    "core_stealth_reviewed_cost.sql",
)


def main():
    docker = local_docker()
    name = "nccu-quick-rehearsal-" + uuid4().hex[:12]
    try:
        start(docker, name)
        sql(
            docker,
            name,
            ROLES
            + BOOTSTRAP
            + "alter table auth.users add column raw_app_meta_data jsonb default '{}';",
        )
        for migration in MIGRATIONS:
            print("Validate", migration, flush=True)
            sql(docker, name, (ROOT / "supabase" / migration).read_text(encoding="utf8"))
        # Existing safety helpers; storage/provider services are outside this harness.
        safety = (ROOT / "supabase/core_user_safety.sql").read_text(encoding="utf8")
        sql(docker, name, safety[: safety.index("create function core_private.group_send_allowed")])
        sql(docker, name, (ROOT / "supabase/core_stealth_quick.sql").read_text(encoding="utf8"))
        result = sql(
            docker,
            name,
            (ROOT / "supabase/core_stealth_quick_smoke.sql").read_text(encoding="utf8"),
        )
        print(result, flush=True)
        print(json.dumps({"passed": True, "network": "none", "persistent_data": False}))
    finally:
        command(docker + ["rm", "--force", name], allow_failure=True)


if __name__ == "__main__":
    main()
