"""Explicit opt-in live smoke against local API + authorized Supabase dev project.

Uses two disposable fixture accounts prepared separately; never prints credentials or tokens.
Does not run as part of pytest and never creates real email accounts or sends mail.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    accounts = json.loads((ROOT / ".local/cloud-smoke-accounts.json").read_text(encoding="utf-8"))
    with httpx.Client(
        base_url="http://127.0.0.1:8000/api/v1", trust_env=False, timeout=30
    ) as client:
        headers = []
        for account in accounts:
            response = client.post(
                "/auth/login", json={"email": account["email"], "password": account["password"]}
            )
            assert response.status_code == 200, ("login", response.status_code, response.text)
            session = response.json()
            assert session["user"]["auth_mode"] == "supabase"
            headers.append({"Authorization": "Bearer " + session["access_token"]})
        host, member = headers[:2]
        value = {
            "content": "雲端 API 測試：兩人一起訂晚餐，費用自行協調。",
            "tags": ["雲端測試"],
            "capacity": 2,
            "deadline_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "price_per_person": "85.50",
            "location": {"name": "虛構測試點", "latitude": 24.987, "longitude": 121.576},
        }
        response = client.post(
            "/posts", json=value, headers={**host, "Idempotency-Key": str(uuid4())}
        )
        assert response.status_code == 201, ("create", response.status_code, response.text)
        post_id = response.json()["id"]
        response = client.post(
            f"/posts/{post_id}/join-requests", json={"expected_version": 1}, headers=member
        )
        assert response.status_code == 201, ("join", response.status_code, response.text)
        request_id = response.json()["id"]
        assert client.get(f"/posts/{post_id}", headers=member).json()["joined"] == 1
        response = client.patch(
            f"/join-requests/{request_id}", json={"status": "accepted"}, headers=host
        )
        assert response.status_code == 200, ("approve", response.status_code, response.text)
        assert client.get(f"/posts/{post_id}", headers=member).json()["joined"] == 2
        response = client.delete(f"/posts/{post_id}/membership", headers=member)
        assert response.status_code == 200 and response.json()["joined"] == 1
        response = client.post(f"/posts/{post_id}/cancel", headers=host)
        assert response.status_code == 200 and response.json()["status"] == "cancelled"
        if len(headers) >= 3:
            response = client.post(
                "/posts", json=value, headers={**host, "Idempotency-Key": str(uuid4())}
            )
            assert response.status_code == 201
            race_post = response.json()["id"]
            pending = [
                client.post(
                    f"/posts/{race_post}/join-requests",
                    json={"expected_version": 1},
                    headers=credential,
                )
                for credential in headers[1:3]
            ]
            assert all(response.status_code == 201 for response in pending)

            def approve(request):
                return client.patch(
                    f"/join-requests/{request.json()['id']}",
                    json={"status": "accepted"},
                    headers=host,
                ).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = list(pool.map(approve, pending))
            assert sorted(statuses) == [200, 409], statuses
            assert client.get(f"/posts/{race_post}", headers=host).json()["joined"] == 2
            assert client.post(f"/posts/{race_post}/cancel", headers=host).status_code == 200
            response = client.post(
                "/posts", json=value, headers={**host, "Idempotency-Key": str(uuid4())}
            )
            race_post = response.json()["id"]

            def edit_or_join(operation):
                if operation == "edit":
                    response = client.patch(
                        f"/posts/{race_post}",
                        json={**value, "capacity": 3, "expected_version": 1},
                        headers=host,
                    )
                else:
                    response = client.post(
                        f"/posts/{race_post}/join-requests",
                        json={"expected_version": 1},
                        headers=member,
                    )
                return 200 if response.status_code == 201 else response.status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = list(pool.map(edit_or_join, ("edit", "join")))
            assert sorted(statuses) == [200, 409], statuses
            assert client.post(f"/posts/{race_post}/cancel", headers=host).status_code == 200
            print("Live PostgreSQL races: last-seat approval and edit-vs-join passed.")
        for credential in headers:
            assert client.post("/auth/logout", headers=credential).status_code == 204
            assert client.get("/auth/me", headers=credential).status_code == 401
        print(
            "Live Supabase Auth + local API: login / publish / pending / approve / "
            "withdraw / cancel / logout passed."
        )


if __name__ == "__main__":
    main()
