"""Opt-in live chat smoke, using the same explicitly prepared disposable fixtures.

Never creates users, sends email, reads real user credentials, or prints tokens.
The created post ID is saved locally for browser checks and targeted cleanup.
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
    if len(accounts) < 3:
        raise ValueError("Three disposable fixtures are required")
    with httpx.Client(
        base_url="http://127.0.0.1:8000/api/v1", trust_env=False, timeout=30
    ) as client:
        credentials = []
        for account in accounts[:3]:
            response = client.post(
                "/auth/login", json={key: account[key] for key in ("email", "password")}
            )
            assert response.status_code == 200, ("fixture login", response.status_code)
            credentials.append({"Authorization": "Bearer " + response.json()["access_token"]})
        host, member, stranger = credentials
        value = {
            "content": "【驗證】文字聊天室：雙帳號確認取餐時間與集合點。",
            "tags": ["臨時驗證"],
            "capacity": 3,
            "deadline_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "price_per_person": "80.00",
            "location": {"name": "虛構取餐測試點", "latitude": 24.987, "longitude": 121.576},
        }
        response = client.post(
            "/posts", json=value, headers={**host, "Idempotency-Key": str(uuid4())}
        )
        assert response.status_code == 201, ("fixture post", response.status_code)
        post_id = response.json()["id"]
        (ROOT / ".local/chat-live-post.json").write_text(
            json.dumps({"post_id": post_id, "owner_id": accounts[0]["id"]}), encoding="utf-8"
        )
        path = f"/conversations/{post_id}"

        def send(credential, content, key=None):
            return client.post(
                path + "/messages",
                json={"content": content},
                headers={**credential, "Idempotency-Key": key or str(uuid4())},
            )

        first = send(host, "【驗證訊息】六點在校門口集合，請在這裡確認。")
        assert first.status_code == 201
        request = client.post(
            f"/posts/{post_id}/join-requests", json={"expected_version": 1}, headers=member
        )
        assert request.status_code == 201
        assert client.get(path + "/messages", headers=member).status_code == 403
        assert client.get(path + "/messages", headers=stranger).status_code == 403
        assert send(stranger, "不應成功的訊息").status_code == 403
        assert client.get("/conversations/activity", headers=host).json()["pending_requests"] == 1
        assert (
            client.patch(
                f"/join-requests/{request.json()['id']}", json={"status": "accepted"}, headers=host
            ).status_code
            == 200
        )
        assert client.get(path + "/messages", headers=member).status_code == 200
        assert client.get("/conversations/activity", headers=member).json()["total_unread"] == 1
        second = send(member, "【驗證訊息】收到，我會準時到。")
        assert second.status_code == 201
        assert (
            client.post(
                path + "/read", json={"message_id": first.json()["id"]}, headers=member
            ).status_code
            == 200
        )
        assert client.get("/conversations/activity", headers=member).json()["total_unread"] == 0
        assert client.get("/conversations/activity", headers=host).json()["total_unread"] == 1
        assert (
            client.post(
                path + "/read", json={"message_id": second.json()["id"]}, headers=host
            ).status_code
            == 200
        )
        key = str(uuid4())
        with ThreadPoolExecutor(max_workers=3) as pool:
            retries = list(
                pool.map(
                    lambda _: send(host, "【驗證訊息】這則重試三次，資料庫只會新增一次。", key),
                    range(3),
                )
            )
        assert all(item.status_code == 201 for item in retries)
        assert len({item.json()["id"] for item in retries}) == 1
        assert len(client.get(path + "/messages", headers=host).json()["messages"]) == 3
        assert client.delete(f"/posts/{post_id}/membership", headers=member).status_code == 200
        assert client.get(path + "/messages", headers=member).status_code == 403
        assert client.get("/conversations/activity", headers=member).json()["groups"] == []
        request = client.post(
            f"/posts/{post_id}/join-requests", json={"expected_version": 1}, headers=member
        )
        assert (
            client.patch(
                f"/join-requests/{request.json()['id']}", json={"status": "accepted"}, headers=host
            ).status_code
            == 200
        )
        print(
            "Live chat passed: membership, private reads, text send, unread/read, "
            "concurrent retries and withdrawal revocation."
        )
        print(
            "One isolated fixture thread retained temporarily for browser verification "
            "and targeted cleanup."
        )


if __name__ == "__main__":
    main()
