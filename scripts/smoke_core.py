"""In-process API smoke test: temporary synthetic accounts, no network."""

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    os.environ["ENVIRONMENT"] = "local"
    os.environ["CORE_LOCAL_ENABLED"] = "true"
    os.environ["API_V1_PREFIX"] = "/api/v1"
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.core_local import dependencies
    from app.core_local.store import CoreStore
    from app.main import app

    local = ROOT / ".local"
    local.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="core-smoke-", dir=local) as temporary:
        store = CoreStore(Path(temporary) / "core.sqlite3")
        get_settings.cache_clear()
        with patch.object(dependencies, "local_store", lambda: store), TestClient(app) as client:
            headers = []
            for name in ("host", "member"):
                response = client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"{name}@example.test",
                        "password": "Synthetic-smoke-only-123!",
                        "display_name": name,
                    },
                )
                assert response.status_code == 201, "register failed"
                headers.append({"Authorization": f"Bearer {response.json()['access_token']}"})
            host, member = headers
            print("PASS: two synthetic local accounts registered")
            response = client.post(
                "/api/v1/posts",
                headers={
                    **host,
                    "Idempotency-Key": str(uuid4()),
                },
                json={
                    "content": "本機自動示範：一起訂晚餐，面交與付款自行協調。",
                    "tags": ["晚餐"],
                    "capacity": 2,
                    "price_per_person": "85.50",
                    "deadline_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "location": {"name": "合成測試點", "latitude": 24.987, "longitude": 121.576},
                },
            )
            assert response.status_code == 201 and response.json()["joined"] == 1
            url = f"/api/v1/posts/{response.json()['id']}"
            print("PASS: publication persisted, host occupies one of two seats")
            response = client.post(
                url + "/join-requests", headers=member, json={"expected_version": 1}
            )
            assert response.status_code == 201 and response.json()["status"] == "pending"
            request_id = response.json()["id"]
            assert client.get(url, headers=member).json()["joined"] == 1
            print("PASS: pending application does not occupy a seat")
            response = client.patch(
                f"/api/v1/join-requests/{request_id}", headers=host, json={"status": "accepted"}
            )
            assert response.status_code == 200
            assert client.get(url, headers=member).json()["joined"] == 2
            print("PASS: host approval fills the group")
            response = client.delete(url + "/membership", headers=member)
            assert response.status_code == 200 and response.json()["joined"] == 1
            print("PASS: withdrawal releases the seat")
            assert client.post("/api/v1/auth/logout", headers=member).status_code == 204
            assert client.get("/api/v1/auth/me", headers=member).status_code == 401
            print("PASS: logout revokes the current session")
        get_settings.cache_clear()
    print("PASS: temporary data cleaned up; no external services used")


if __name__ == "__main__":
    main()
