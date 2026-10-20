"""Opt-in isolated live API checks: three-party races, withdrawal, expiry, lost replies.

Prepare three scoped accounts using smoke_stealth_cloud.prepare with STATE pointing
at .local/stealth-resilience; review/execute that scope's SQL with the dev-only MCP.
This runner uses normal user sessions, never prints passwords or deploys anything.
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx

STATE = Path(__file__).resolve().parents[1] / ".local" / "stealth-resilience"


def run():
    manifest = json.loads((STATE / "accounts.json").read_text(encoding="utf-8"))
    accounts, scope = manifest["accounts"], manifest["scope"]
    assert scope.startswith("stealth-e2e-") and len(accounts) == 3
    for account in accounts:
        UUID(account["id"])
        assert account["email"].startswith(scope + "-") and account["email"].endswith(
            "@example.test"
        )
    results = []
    headers = []
    base = "/stealth-cloud"
    with httpx.Client(base_url="http://127.0.0.1:8000/api/v1", trust_env=False, timeout=120) as c:

        def call(method, path, actor=0, value=None, expected=200, key=None):
            r = c.request(
                method,
                path,
                headers={**headers[actor], **({"Idempotency-Key": key} if key else {})},
                json=value,
            )
            assert r.status_code == expected, f"{method} {path}: {r.status_code} != {expected}"
            return r.json() if r.content else None

        def passed(label):
            results.append(label)
            print("PASS " + label, flush=True)

        try:
            for account in accounts:
                r = c.post("/auth/login", json={k: account[k] for k in ("email", "password")})
                assert r.status_code == 200
                session = r.json()
                assert session["user"]["id"] == account["id"]
                headers.append({"Authorization": "Bearer " + session["access_token"]})
            for actor in range(3):
                call(
                    "PUT",
                    base + "/agent",
                    actor,
                    {
                        "name": "隔離三人韌性分身",
                        "enabled": True,
                        "include_demo": False,
                        "daily_invite_limit": 10,
                        "timezone": "Asia/Taipei",
                        "quiet_start_hour": None,
                        "quiet_end_hour": None,
                        "exploration": "balanced",
                        "tone": "warm",
                        "avatar_key": "cat",
                        "expected_version": 0,
                    },
                )

            def group():
                start = datetime.now(UTC)
                signals = []
                for actor in range(3):
                    signal = call(
                        "POST",
                        base + "/signals",
                        actor,
                        {
                            "kind": "need",
                            "category": "pizza",
                            "acceptable_categories": [],
                            "title": "【隔離韌性】一起分食披薩，沒有真實付款",
                            "location_id": "south_gate",
                            "starts_at": (start + timedelta(hours=1)).isoformat(),
                            "ends_at": (start + timedelta(hours=3)).isoformat(),
                            "expires_at": (start + timedelta(hours=3)).isoformat(),
                            "budget_max_twd": 150,
                            "known_cost_twd": None,
                            "tags": [scope[-8:]],
                            "roles": ["share", "order", "pickup"],
                            "dietary_requirements": [],
                            "dietary_verified": [],
                        },
                        expected=201,
                        key=str(uuid4()),
                    )
                    signals.append(signal["signal"]["id"])
                matches = call("GET", base + "/matches")["items"]
                triple = next(
                    (m for m in matches if m["member_count"] == 3 and m["status"] == "pending"),
                    None,
                )
                if triple is None:
                    # Honor the real cooldown, never rewrite clocks/limits in the database.
                    print("Waiting up to 31s for normal matching cooldown", flush=True)
                    time.sleep(31)
                    call("POST", base + "/matches/run", value={"max_proposals": 3})
                    triple = next(
                        m
                        for m in call("GET", base + "/matches")["items"]
                        if m["member_count"] == 3 and m["status"] == "pending"
                    )
                path = base + "/matches/" + triple["id"]
                for actor in range(3):
                    call("POST", path + "/decision", actor, {"decision": "accepted"})
                return path, signals

            def terms(deadline):
                return {
                    "content": "【隔離韌性驗證】三人共同確認，沒有真實訂購或付款。",
                    "cost_per_person_twd": 90,
                    "deadline_at": deadline.isoformat(),
                    "order_member": 1,
                    "pickup_member": 2,
                    "cost_change_note": "",
                }

            path, signals = group()
            proposal = terms(datetime.now(UTC) + timedelta(minutes=20))

            def edit(actor):
                r = c.put(
                    path + "/terms",
                    headers=headers[actor],
                    json={
                        "expected_revision": 0,
                        "value": {**proposal, "cost_per_person_twd": 90 + actor},
                    },
                )
                return r.status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = list(pool.map(edit, [0, 1]))
            assert sorted(statuses) == [200, 409], statuses
            passed("simultaneous edits: one winner, one version conflict")
            one = call("POST", path + "/consent", 0, {"expected_revision": 1, "agree": True})
            assert one["confirmed_count"] == 1 and not one["formed_post_id"]
            call("POST", path + "/decision", 2, {"decision": "declined"})
            call(
                "POST", path + "/consent", 1, {"expected_revision": 1, "agree": True}, expected=409
            )
            passed("one consent cannot form; member withdrawal prevents formation")
            for actor, signal in enumerate(signals):
                call("DELETE", base + "/signals/" + signal, actor)

            # New signals, not revived withdrawn invitations.
            print("Respecting cooldown before second isolated scenario", flush=True)
            time.sleep(31)
            path, _ = group()
            call(
                "PUT",
                path + "/terms",
                value={
                    "expected_revision": 0,
                    "value": terms(datetime.now(UTC) + timedelta(seconds=4)),
                },
            )
            time.sleep(5)
            call(
                "POST",
                path + "/consent",
                value={"expected_revision": 1, "agree": True},
                expected=409,
            )
            passed("real deadline expiry rejects consent without changing server clocks")
            revised = call(
                "PUT",
                path + "/terms",
                value={
                    "expected_revision": 1,
                    "value": terms(datetime.now(UTC) + timedelta(minutes=20)),
                },
            )
            assert revised["confirmed_count"] == 0 and revised["terms_revision"] == 2
            for actor in [0, 1]:
                pending = call(
                    "POST", path + "/consent", actor, {"expected_revision": 2, "agree": True}
                )
                assert pending["formed_post_id"] is None
            # A lost-response simulation: server commits, client discards response and
            # raises a local ReadError; retry the identical request, no OS/network changes.
            try:
                call("POST", path + "/consent", 2, {"expected_revision": 2, "agree": True})
                raise httpx.ReadError("synthetic lost response after commit")
            except httpx.ReadError:
                formed = call("POST", path + "/consent", 2, {"expected_revision": 2, "agree": True})
            post_id = formed["formed_post_id"]
            assert post_id and formed["confirmed_count"] == 3
            again = call("POST", path + "/consent", 2, {"expected_revision": 2, "agree": True})
            assert again["formed_post_id"] == post_id
            for actor in range(3):
                post = call("GET", "/posts/" + post_id, actor)
                assert post["joined"] == post["capacity"] == 3
                call("GET", "/conversations/" + post_id + "/messages", actor)
            passed("three current consents form one 3/3 post; lost-reply retry returns same post")
            key = str(uuid4())
            chat = "/conversations/" + post_id + "/messages"
            first = call("POST", chat, value={"content": "隔離斷線重試訊息"}, expected=201, key=key)
            repeated = call(
                "POST", chat, value={"content": "隔離斷線重試訊息"}, expected=201, key=key
            )
            assert first["id"] == repeated["id"]
            assert len(call("GET", chat, 2)["messages"]) == 1
            passed("message retry with same idempotency key does not duplicate")
            after_exit = call("DELETE", "/posts/" + post_id + "/membership", 1)
            assert after_exit["joined"] == 2
            call("GET", chat, 1, expected=403)
            passed("formed member withdrawal releases one seat and removes chat access")
            (STATE / "report.json").write_text(
                json.dumps(
                    {
                        "scope": scope,
                        "passed": results,
                        "post_id": post_id,
                        "proposal_id": path.rsplit("/", 1)[-1],
                        "generated_at": datetime.now(UTC).isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        finally:
            for actor in range(len(headers)):
                call("POST", "/auth/logout", actor, expected=204)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run"], nargs="?", default="run")
    if parser.parse_args().action == "prepare":
        sys.path.insert(0, str(STATE.parents[1]))
        from scripts import smoke_stealth_cloud

        smoke_stealth_cloud.STATE = STATE
        smoke_stealth_cloud.FIXTURE = STATE / "accounts.json"
        smoke_stealth_cloud.prepare()
    else:
        run()
