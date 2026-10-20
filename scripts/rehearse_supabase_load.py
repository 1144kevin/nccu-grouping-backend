"""Bounded live local-API/Supabase rehearsal, with disposable scoped accounts.

prepare writes private reviewed SQL; measure/run never provision via admin keys.
No email, models, deployment, arbitrary hosts or unbounded traffic.
"""

import argparse
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local" / "supabase-load"
BASE = "http://127.0.0.1:8000/api/v1"


def manifest():
    value = json.loads((STATE / "accounts.json").read_text(encoding="utf-8"))
    if not re.fullmatch(r"stealth-e2e-[0-9a-f]{32}", value["scope"]) or len(value["accounts"]) != 3:
        raise ValueError("Invalid disposable fixture scope")
    for account in value["accounts"]:
        UUID(account["id"])
        if account["email"] != f"{value['scope']}-{value['accounts'].index(account)}@example.test":
            raise ValueError("Refusing non-fixture account")
    return value


def prepare():
    from scripts import smoke_stealth_cloud as fixture

    fixture.STATE, fixture.FIXTURE = STATE, STATE / "accounts.json"
    fixture.prepare()
    value = manifest()
    ids = ",".join(f"'{a['id']}'::uuid" for a in value["accounts"])
    host, scope = value["accounts"][0]["id"], value["scope"]
    tag = "perf-" + scope[-16:]
    guard = f"""do $guard$ begin
      if (select count(*) from auth.users where id=any(array[{ids}])
        and raw_app_meta_data->>'stealth_test_scope'='{scope}')<>3 then
        raise exception 'Fixture scope mismatch'; end if;
      end $guard$;"""
    for lower, upper in ((1, 1000), (1001, 5000)):
        sql = f"""begin; set local lock_timeout='2s'; set local statement_timeout='15s';
{guard}
insert into public.core_profiles(id,display_name) select id,'隔離效能測試'
from auth.users where id=any(array[{ids}]) on conflict do nothing;
-- Blocks belong only to disposable owners; existing users cannot see/interact with fixtures.
insert into public.core_user_blocks(owner_id,blocked_id)
select owner.id,other.id from public.core_profiles owner cross join public.core_profiles other
where owner.id=any(array[{ids}]) and not other.id=any(array[{ids}]) on conflict do nothing;
insert into public.core_posts(id,author_id,payload,original_payload,capacity,deadline_at,
 request_key,demo_batch,demo_owner_id,cancelled)
select md5('{scope}:'||n)::uuid,'{host}',v,v,4,now()+interval '2 hours',
  'perf-{scope}-'||n,'{scope}','{host}',n%10=0 from generate_series({lower},{upper}) n
cross join lateral(select jsonb_build_object('content','【隔離效能】{scope} 合成拼單 '||n,
 'tags',jsonb_build_array('{tag}'),'capacity',4,'deadline_at',now()+interval '2 hours',
 'price_per_person',90,'location',jsonb_build_object('name','隔離效能集合點',
 'latitude',24.987,'longitude',121.576)) v) payload
on conflict(id) do nothing;
commit;
select count(*) as fixture_posts from public.core_posts
 where author_id='{host}' and demo_batch='{scope}';
"""
        (STATE / f"seed-{upper}.sql").write_text(sql, encoding="utf-8")
    print("Prepared scoped 1,000/5,000-row SQL. Credentials remain in ignored .local only.")


def percentile(values, percentile):
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * percentile) - 1)], 2)


def summarize(samples):
    result = {}
    for path in sorted({s["path"] for s in samples}):
        group = [s for s in samples if s["path"] == path]
        times = [s["ms"] for s in group]
        result[path] = {
            "requests": len(group),
            "p50_ms": percentile(times, 0.5),
            "p95_ms": percentile(times, 0.95),
            "max_ms": round(max(times), 2),
            "errors": sum(s["status"] != 200 for s in group),
        }
    return result


def run(duration=0, rate=1, stage="baseline"):
    if not 0 <= duration <= 180 or rate not in (1, 2):
        raise ValueError("Duration 0..180 seconds, rate 1 or 2; never a capacity limit test")
    if stage not in ("baseline", "1000", "5000", "sustained"):
        raise ValueError("Invalid stage")
    value = manifest()
    sessions = []
    with httpx.Client(base_url=BASE, trust_env=False, timeout=15, follow_redirects=False) as client:
        for account in value["accounts"]:
            response = client.post(
                "/auth/login", json={k: account[k] for k in ("email", "password")}
            )
            if response.status_code != 200:
                raise RuntimeError(f"Fixture login status {response.status_code}; no body printed")
            data = response.json()
            if data["user"]["id"] != account["id"] or data["user"]["auth_mode"] != "supabase":
                raise RuntimeError("Fixture/session identity mismatch")
            sessions.append({"Authorization": "Bearer " + data["access_token"]})
        actor = sessions[1]
        paths = [
            "/posts?scope=feed&active_only=true&limit=20&q=" + value["scope"],
            "/recommendations?limit=20",
            "/search?scope=history&q=" + value["scope"] + "&limit=20",
        ]
        samples = []

        def sample(path):
            started = time.perf_counter()
            try:
                response = client.get(
                    path, headers=sessions[0] if path.startswith("/search") else actor
                )
                code = response.status_code
                if code == 200:
                    data = response.json()
                    if not isinstance(data.get("items"), list):
                        code = 599
            except (httpx.HTTPError, ValueError):
                code = 599
            return {
                "path": path.split("?")[0],
                "status": code,
                "ms": (time.perf_counter() - started) * 1000,
            }

        # Warm-up is separately excluded from steady-state latency.
        warmup = [sample(path) for path in paths]
        if any(s["status"] != 200 for s in warmup):
            raise RuntimeError("Warm-up failed; no sustained load sent")
        started = time.monotonic()
        stopped = None
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = []
            total = math.ceil(duration * rate) if duration else 30
            for i in range(total):
                if duration:
                    delay = started + i / rate - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                # No unbounded client backlog: wait for one of at most two requests.
                if len(pending) >= 2:
                    samples.append(pending.pop(0).result())
                if sum(s["status"] != 200 for s in samples) >= 3 or any(
                    s["ms"] > 10000 for s in samples[-3:]
                ):
                    stopped = "three errors or request latency over 10s"
                    break
                pending.append(pool.submit(sample, paths[i % len(paths)]))
            samples.extend(f.result() for f in pending)
        report = {
            "scope": value["scope"],
            "stage": stage,
            "transport": "Windows loopback API -> live scoped Supabase Auth + RPC",
            "seconds": round(time.monotonic() - started, 2),
            "max_inflight": 2,
            "target_rps": rate if duration else None,
            "stopped": stopped,
            "warmup": summarize(warmup),
            "endpoints": summarize(samples),
            "includes_auth_network_rpc": True,
            "models_called": False,
        }
        (STATE / f"{stage}-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        if stopped or any(s["status"] != 200 for s in samples):
            raise SystemExit("Load stopped/failed; review report before retrying")


def last_seat():
    value = manifest()
    with httpx.Client(base_url=BASE, trust_env=False, timeout=20) as client:
        headers = []
        for account in value["accounts"]:
            r = client.post("/auth/login", json={k: account[k] for k in ("email", "password")})
            if r.status_code != 200:
                raise RuntimeError("Fixture login failed")
            if r.json()["user"]["id"] != account["id"]:
                raise RuntimeError("Fixture identity mismatch")
            headers.append({"Authorization": "Bearer " + r.json()["access_token"]})
        r = client.post(
            "/posts",
            headers={**headers[0], "Idempotency-Key": str(uuid4())},
            json={
                "content": "【隔離效能】最後名額實際 Supabase 併發測試 " + value["scope"],
                "tags": ["perf-" + value["scope"][-16:]],
                "capacity": 2,
                "deadline_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "price_per_person": 90,
                "location": {"name": "隔離效能測試", "latitude": 24.987, "longitude": 121.576},
            },
        )
        if r.status_code != 201:
            raise RuntimeError(f"Create status {r.status_code}")
        post = r.json()
        requests = []
        for member in (1, 2):
            post = client.get(f"/posts/{post['id']}", headers=headers[member]).json()
            r = client.post(
                f"/posts/{post['id']}/join-requests",
                headers=headers[member],
                json={"expected_version": post["version"]},
            )
            if r.status_code != 201:
                raise RuntimeError(f"Join status {r.status_code}")
            requests.append(r.json()["id"])

        def accept(request):
            r = client.patch(
                f"/join-requests/{request}", headers=headers[0], json={"status": "accepted"}
            )
            return r.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(accept, requests))
        final = client.get(f"/posts/{post['id']}", headers=headers[0]).json()
        assert sorted(outcomes) == [200, 409] and final["joined"] == 2
        sent = client.post(
            f"/conversations/{post['id']}/messages",
            headers={**headers[0], "Idempotency-Key": str(uuid4())},
            json={"content": "隔離備份還原：聊天室關聯資料。"},
        )
        if sent.status_code != 201:
            raise RuntimeError(f"Fixture chat status {sent.status_code}")
        report = {
            "transport": "local API + actual Supabase concurrent transactions",
            "attempts": 2,
            "accepted": 1,
            "rejected_full": 1,
            "joined": final["joined"],
            "oversold": False,
        }
        (STATE / "last-seat-report.json").write_text(json.dumps(report, indent=2), encoding="utf8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "measure", "run", "last-seat"))
    parser.add_argument("--stage", default="baseline")
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--rps", type=int, choices=(1, 2), default=1)
    options = parser.parse_args()
    if options.action == "prepare":
        prepare()
    elif options.action == "last-seat":
        last_seat()
    else:
        run(options.seconds if options.action == "run" else 0, options.rps, options.stage)
