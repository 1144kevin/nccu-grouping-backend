"""Explicit live v2 recommendation acceptance via local API and disposable Auth accounts.

prepare writes reviewed provisioning/cleanup SQL; run uses normal user sessions only.
Never deploys, calls a model, provisions users through an admin API or prints credentials.
"""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local" / "recommendations-e2e"


def fixture_tool():
    from scripts import smoke_stealth_cloud

    smoke_stealth_cloud.STATE = STATE
    smoke_stealth_cloud.FIXTURE = STATE / "accounts.json"
    return smoke_stealth_cloud


def run():
    manifest = json.loads((STATE / "accounts.json").read_text(encoding="utf-8"))
    scope, accounts = manifest["scope"], manifest["accounts"]
    if not scope.startswith("stealth-e2e-") or len(accounts) != 3:
        raise SystemExit("Invalid disposable fixture scope")
    for account in accounts:
        UUID(account["id"])
        if not account["email"].startswith(scope + "-") or not account["email"].endswith(
            "@example.test"
        ):
            raise SystemExit("Refusing non-fixture credentials")
    steps = []

    def passed(label):
        steps.append(label)
        print("PASS " + label, flush=True)

    with httpx.Client(
        base_url="http://127.0.0.1:8000/api/v1", trust_env=False, timeout=30
    ) as client:
        headers = []

        def call(method, path, actor=0, value=None, expected=200, key=False):
            options = {"headers": dict(headers[actor])}
            if value is not None:
                options["json"] = value
            if key:
                options["headers"]["Idempotency-Key"] = str(uuid4())
            response = client.request(method, path, **options)
            assert response.status_code == expected, (
                f"{method} {path}: {response.status_code} != {expected}"
            )
            return response.json() if response.content else None

        assert client.get("/recommendations").status_code == 401
        for account in accounts:
            response = client.post(
                "/auth/login", json={k: account[k] for k in ("email", "password")}
            )
            assert response.status_code == 200, f"fixture login: {response.status_code}"
            session = response.json()
            assert (
                session["user"]["id"] == account["id"]
                and session["user"]["auth_mode"] == "supabase"
            )
            headers.append({"Authorization": "Bearer " + session["access_token"]})
        passed("real isolated Supabase sessions; anonymous recommendation denied")
        now = datetime.now(UTC)
        tag = "隔離推薦" + scope[-8:]
        location = "隔離測試集合點"
        preferences = call("GET", "/account/preferences")
        value = {k: preferences[k] for k in ("notifications", "preferred_tags")}
        value["preferred_tags"] = [tag, location]
        updated = call(
            "PUT",
            "/account/preferences",
            value={"value": value, "expected_version": preferences["version"]},
        )
        ids = []
        for i in range(4):
            post = call(
                "POST",
                "/posts",
                1,
                {
                    "content": f"【隔離驗證】推薦排序情境 {i + 1}，沒有真實訂購或付款。",
                    "tags": [tag],
                    "capacity": 2 if i in (1, 3) else 3,
                    "deadline_at": (
                        now + (timedelta(minutes=15) if i == 0 else timedelta(days=1))
                    ).isoformat(),
                    "price_per_person": "90.00",
                    "location": {"name": location, "latitude": 24.987, "longitude": 121.576},
                },
                expected=201,
                key=True,
            )
            ids.append(post["id"])
        for index in (0, 3):
            join = call(
                "POST",
                f"/posts/{ids[index]}/join-requests",
                2,
                {"expected_version": 1},
                expected=201,
            )
            call("PATCH", f"/join-requests/{join['id']}", 1, {"status": "accepted"})
        first = call("GET", "/recommendations?limit=30")
        assert [p["id"] for p in first["items"][:3]] == ids[:3]
        assert [p["recommendation"]["score"] for p in first["items"][:3]] == [48, 40, 36]
        assert [p["recommendation"]["remaining_seats"] for p in first["items"][:3]] == [1, 1, 2]
        codes = {r["code"] for r in first["items"][0]["recommendation"]["reasons"]}
        assert codes == {"preferred_tags", "preferred_location", "remaining_seats", "deadline"}
        passed("preferences, location, seats and deadline yield expected order and four reasons")
        seen = set()
        page, pages = first, 0
        while True:
            pages += 1
            assert pages <= 20
            for post in page["items"]:
                assert post["id"] not in seen
                seen.add(post["id"])
                assert post["status"] == "open" and post["joined"] < post["capacity"]
                assert post["recommendation"]["version"] == "rules_v2"
                assert (
                    post["recommendation"]["remaining_seats"] == post["capacity"] - post["joined"]
                )
            if not page["next_cursor"]:
                break
            page = call(
                "GET",
                "/recommendations?"
                + str(httpx.QueryParams({"limit": 30, "cursor": page["next_cursor"]})),
            )
        assert ids[3] not in seen and len(seen) > 30
        passed("large demonstration feed paginates without duplicates or full/closed groups")
        # Recommendations omit closed examples; exercise the latest feed as well so
        # malformed demo records cannot hide behind recommendation eligibility.
        latest_cursor = None
        latest_ids = set()
        demo_count = 0
        for _ in range(20):
            params = {"limit": 50, "scope": "feed"}
            if latest_cursor:
                params["cursor"] = latest_cursor
            latest = call("GET", "/posts?" + str(httpx.QueryParams(params)))
            for post in latest["items"]:
                assert post["id"] not in latest_ids
                latest_ids.add(post["id"])
                assert post["location"].get("source") in (None, "openstreetmap")
                demo_count += bool(post["is_demo"])
            latest_cursor = latest["next_cursor"]
            if not latest_cursor:
                break
        else:
            raise AssertionError("Latest feed did not terminate")
        assert demo_count >= 120
        passed("latest feed validates all large demo records including full and closed examples")
        cursor_path = "/recommendations?" + str(
            httpx.QueryParams({"limit": 30, "cursor": first["next_cursor"]})
        )
        call("GET", cursor_path, 2, expected=409)
        call("PUT", f"/account/saved-posts/{ids[1]}", value={"hidden": True, "reminder": False})
        call("GET", cursor_path, expected=409)
        hidden = call("GET", "/recommendations?limit=30")
        assert ids[1] not in {p["id"] for p in hidden["items"]}
        passed("cross-account cursor denied and hiding refreshes the candidate inventory")
        next_cursor = hidden["next_cursor"]
        call(
            "PUT",
            "/account/preferences",
            value={
                "value": {**value, "preferred_tags": [location]},
                "expected_version": updated["version"],
            },
        )
        call(
            "GET",
            "/recommendations?" + str(httpx.QueryParams({"cursor": next_cursor})),
            expected=409,
        )
        changed = call("GET", "/recommendations?limit=30")
        selected = next(p for p in changed["items"] if p["id"] == ids[0])
        assert "preferred_tags" not in {r["code"] for r in selected["recommendation"]["reasons"]}
        assert selected["recommendation"]["score"] == 28
        passed("saved preference changes invalidate old cursor and update reasons/score")
        old_cursor = changed["next_cursor"]
        join = call(
            "POST", f"/posts/{ids[0]}/join-requests", value={"expected_version": 1}, expected=201
        )
        call("PATCH", f"/join-requests/{join['id']}", 1, {"status": "accepted"})
        call(
            "GET",
            "/recommendations?" + str(httpx.QueryParams({"cursor": old_cursor})),
            expected=409,
        )
        refreshed = call("GET", "/recommendations?limit=30")
        assert ids[0] not in {p["id"] for p in refreshed["items"]}
        passed("filling last seat invalidates cursor and removes newly full group")
        # Preserve normal latest/detail behavior: full posts still exist and can be read.
        assert call("GET", f"/posts/{ids[0]}")["status"] == "full"
        passed("existing full-post detail remains available outside recommendations")
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "scope": scope,
            "passed": steps,
            "fixture_post_ids": ids,
            "initial_recommended_count": len(seen),
            "pages": pages,
            "validated_demo_records": demo_count,
            "top_three_scores": [48, 40, 36],
            "reason_codes": sorted(codes),
        }
        (STATE / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for i in range(3):
            call("POST", "/auth/logout", i, expected=204)
        print(
            "Live recommendation acceptance passed. Run cleanup SQL, then erase local credentials."
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run", "forget-credentials"])
    action = parser.parse_args().action
    if action == "prepare":
        fixture_tool().prepare()
    elif action == "run":
        run()
    else:
        for name in ("accounts.json", "prepare.sql"):
            (STATE / name).unlink(missing_ok=True)
        print("Disposable local credentials removed; safe evidence retained.")


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
