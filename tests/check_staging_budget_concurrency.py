"""Run only against the disposable, network-isolated test container."""

import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CONTAINER = "nccu-ai-isolated-test"


def psql(sql):
    return subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            CONTAINER,
            "psql",
            "-U",
            "postgres",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=sql,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout.strip()


def main():
    psql(Path("supabase/openai_budget_staging.sql").read_text(encoding="utf8"))
    with ThreadPoolExecutor(max_workers=20) as pool:
        values = list(
            pool.map(
                lambda _: psql("select ai_budget_private.reserve('text', 1000000);"), range(20)
            )
        )
    assert values.count("t") == 10 and values.count("f") == 10, values
    assert psql("select sum(units) from ai_budget_private.reservations;") == "10000000"
    psql("update ai_budget_private.reservations set utc_day = utc_day - 1;")
    assert psql("select ai_budget_private.reserve('speech', 1);") == "t"
    psql("update ai_budget_private.reservations set units=5000000, utc_day=utc_day-1;")
    assert psql("select ai_budget_private.reserve('text', 1);") == "f"
    print("PASS isolated PostgreSQL concurrent requests, daily cap, persistence and total cap")


if __name__ == "__main__":
    main()
