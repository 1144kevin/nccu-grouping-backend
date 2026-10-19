"""Prepare disposable data for manual App recommendation/join/chat acceptance.

prepare writes provisioning/cleanup SQL using the shared disposable-account helper.
setup uses only normal user sessions on the local API; it never performs the UI
actions being verified. Credentials remain in the ignored fixture directory.
"""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx

STATE = Path(__file__).resolve().parents[1] / ".local" / "recommendations-ui-e2e"


def setup():
    if (STATE / "posts.json").exists():
        raise SystemExit("Existing UI fixture posts: clean up this scope first")
    manifest = json.loads((STATE / "accounts.json").read_text(encoding="utf-8"))
    accounts, scope = manifest["accounts"], manifest["scope"]
    if not scope.startswith("stealth-e2e-") or len(accounts) != 3:
        raise SystemExit("Invalid disposable scope")
    for account in accounts:
        UUID(account["id"])
        if not account["email"].startswith(scope + "-") or not account["email"].endswith(
            "@example.test"
        ):
            raise SystemExit("Refusing non-fixture account")
    with httpx.Client(
        base_url="http://127.0.0.1:8000/api/v1", trust_env=False, timeout=30
    ) as client:
        headers = []

        def call(method, path, actor=0, value=None, expected=200):
            response = client.request(
                method,
                path,
                headers={**headers[actor], "Idempotency-Key": str(uuid4())},
                json=value,
            )
            assert response.status_code == expected, (
                f"{method} {path}: {response.status_code}, expected {expected}"
            )
            return response.json() if response.content else None

        try:
            for account in accounts:
                response = client.post(
                    "/auth/login", json={k: account[k] for k in ("email", "password")}
                )
                assert response.status_code == 200, "Fixture login failed"
                session = response.json()
                assert session["user"]["id"] == account["id"]
                headers.append({"Authorization": "Bearer " + session["access_token"]})
            preferences = call("GET", "/account/preferences")
            call(
                "PUT",
                "/account/preferences",
                value={
                    "value": {
                        "notifications": preferences["notifications"],
                        "preferred_tags": ["隔離推薦", "隔離測試集合點"],
                    },
                    "expected_version": preferences["version"],
                },
            )
            posts = []
            now = datetime.now(UTC)
            for index, name in enumerate(("A 優先推薦待加入", "B 尚有兩席", "C 已滿團排除")):
                posts.append(
                    call(
                        "POST",
                        "/posts",
                        1,
                        {
                            "content": f"【隔離畫面驗證】{name}。沒有真實訂購或付款。",
                            "tags": ["隔離推薦"],
                            "capacity": 3 if index == 1 else 2,
                            "deadline_at": (
                                now + (timedelta(minutes=25) if index == 0 else timedelta(days=1))
                            ).isoformat(),
                            "price_per_person": "90.00",
                            "location": {
                                "name": "隔離測試集合點",
                                "latitude": 24.987,
                                "longitude": 121.576,
                            },
                        },
                        expected=201,
                    )
                )
            join = call(
                "POST",
                f"/posts/{posts[2]['id']}/join-requests",
                2,
                {"expected_version": posts[2]["version"]},
                expected=201,
            )
            call("PATCH", f"/join-requests/{join['id']}", 1, {"status": "accepted"})
            (STATE / "posts.json").write_text(
                json.dumps({"scope": scope, "posts": posts}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print("Prepared A (48 points), B (36 points), C (full; excluded).")
            print("Perform join, approval, and chat through the App UI; then run cleanup.sql.")
        finally:
            for actor in range(len(headers)):
                call("POST", "/auth/logout", actor, expected=204)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "setup"])
    action = parser.parse_args().action
    if action == "prepare":
        sys.path.insert(0, str(STATE.parents[1]))
        from scripts import smoke_stealth_cloud

        smoke_stealth_cloud.STATE = STATE
        smoke_stealth_cloud.FIXTURE = STATE / "accounts.json"
        smoke_stealth_cloud.prepare()
    else:
        setup()
