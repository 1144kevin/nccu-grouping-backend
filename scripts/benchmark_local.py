"""Bounded synthetic SQLite rehearsal. No network, cloud data, models or deployment."""

import argparse
import json
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(posts=500, attempts=8):
    from fastapi import HTTPException

    from app.core_local.schemas import Location, PostInput, RegisterInput
    from app.core_local.store import CoreStore

    if not 10 <= posts <= 2000 or not 2 <= attempts <= 16:
        raise ValueError("Use 10..2000 posts and 2..16 join attempts")
    state = ROOT / ".local"
    state.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="benchmark-", dir=state) as folder:
        store = CoreStore(Path(folder) / "isolated.sqlite3")
        users = [
            store.register(
                RegisterInput(
                    email=f"bench-{uuid4()}@example.test",
                    password="Synthetic-benchmark-only-123!",
                    display_name=f"合成效能{i}",
                )
            ).user.id
            for i in range(attempts + 1)
        ]
        value = PostInput(
            content="合成效能測試，不會實際訂購或付款。",
            tags=["效能測試"],
            deadline_at=datetime.now(UTC) + timedelta(hours=1),
            capacity=2,
            location=Location(name="測試正門", latitude=24.987, longitude=121.576),
            price_per_person=90,
        )
        started = time.perf_counter()
        for _ in range(posts):
            target = store.create_post(users[0], value, str(uuid4()))
        seed_seconds = time.perf_counter() - started
        timings = []
        for _ in range(30):
            started = time.perf_counter()
            page = store.posts(users[1], "all", 20, None)
            timings.append((time.perf_counter() - started) * 1000)
            assert len(page.items) == 20 if posts >= 20 else len(page.items) == posts
        requests = [store.request_join(user, target.id, target.version) for user in users[1:]]

        def accept(join):
            try:
                store.decide(users[0], join.id, "accepted")
                return "accepted"
            except HTTPException as error:
                if error.status_code == 409:
                    return "full"
                raise

        with ThreadPoolExecutor(max_workers=attempts) as pool:
            results = list(pool.map(accept, requests))
        assert results.count("accepted") == 1
        assert store.post(users[0], target.id).joined == 2
        ordered = sorted(timings)
        report = {
            "adapter": "disposable SQLite; NOT Supabase/PostgreSQL capacity",
            "posts": posts,
            "seed_seconds": round(seed_seconds, 3),
            "feed_samples": len(timings),
            "feed_p50_ms": round(statistics.median(timings), 3),
            "feed_p95_ms": round(ordered[int(len(ordered) * 0.95) - 1], 3),
            "last_seat_attempts": attempts,
            "accepted": results.count("accepted"),
            "rejected_full": results.count("full"),
            "oversold": False,
        }
    (state / "benchmark-latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--posts", type=int, default=500)
    parser.add_argument("--attempts", type=int, default=8)
    args = parser.parse_args()
    print(json.dumps(run(args.posts, args.attempts), ensure_ascii=False, indent=2))
