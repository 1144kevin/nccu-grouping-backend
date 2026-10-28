import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.api.routes import ai
from app.core.config import Settings
from app.core_cloud.writing_jobs import WritingJobs
from app.core_local import dependencies
from app.core_local.store import CoreStore
from app.main import create_app


@pytest.fixture
def job_api(tmp_path, monkeypatch):
    settings = Settings(core_local_enabled=True, core_supabase_enabled=False, environment="local")
    store = CoreStore(tmp_path / "isolated.sqlite3")
    jobs = WritingJobs()
    release, entered = threading.Event(), threading.Event()
    calls = []

    class Provider:
        def extract(self, *_):
            calls.append("inference")
            entered.set()
            assert release.wait(5)
            return json.dumps(
                {
                    "text": (
                        "這是隔離測試的合成文案，想找朋友一起討論餐點、費用與取餐方式。"
                        "有興趣可以先留言確認需求，談妥時間和分工後，再決定是否參加拼單。"
                    )
                }
            )

    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(ai, "configured_provider", lambda _: Provider())
    monkeypatch.setattr(ai, "writing_jobs", jobs)
    dependencies._attempts.clear()
    ai.quota.entries.clear()
    app = create_app(settings)
    app.dependency_overrides[dependencies.get_store] = lambda: store
    try:
        with TestClient(app) as client:
            accounts = []
            for index in range(5):
                response = client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"actor{index}@example.test",
                        "display_name": f"actor{index}",
                        "password": "Synthetic-account-123!",
                    },
                )
                assert response.status_code == 201
                session = response.json()
                accounts.append(
                    (
                        session["user"]["id"],
                        {
                            "Authorization": "Bearer " + session["access_token"],
                            "Idempotency-Key": "same-owned-request-key",
                        },
                    )
                )
            yield client, jobs, accounts, store, release, entered, calls
    finally:
        release.set()
        jobs.close()
        dependencies._attempts.clear()
        ai.quota.entries.clear()


def test_five_sessions_get_owned_jobs_and_cannot_read_or_cancel_each_other(job_api):
    client, jobs, accounts, _, release, entered, calls = job_api
    value = {"mode": "voice", "content": "想找人一起討論餐點與取餐"}
    submitted = []
    for _, headers in accounts:
        response = client.post("/api/v1/ai/post-writing/jobs", headers=headers, json=value)
        assert response.status_code == 202
        submitted.append(response.json())
    assert entered.wait(1)
    url = "/api/v1/ai/post-writing/jobs/" + submitted[1]["id"]
    assert client.get(url, headers=accounts[0][1]).status_code == 404
    assert client.delete(url, headers=accounts[0][1]).status_code == 404
    assert client.get(url).status_code == 401
    assert client.get(url, headers=accounts[1][1]).json()["result"] is None
    repeated = client.post("/api/v1/ai/post-writing/jobs", headers=accounts[0][1], json=value)
    assert repeated.json()["id"] == submitted[0]["id"]
    changed = client.post(
        "/api/v1/ai/post-writing/jobs",
        headers=accounts[0][1],
        json={**value, "content": "另一份需求"},
    )
    assert changed.status_code == 409
    release.set()
    with jobs.condition:
        assert jobs.condition.wait_for(
            lambda: all(j.finished is not None for j in jobs.jobs.values()), 3
        )
    assert len(calls) == 5
    for (_, headers), job in zip(accounts, submitted, strict=True):
        result = client.get("/api/v1/ai/post-writing/jobs/" + job["id"], headers=headers)
        assert result.status_code == 200 and result.json()["status"] == "succeeded"
        assert result.headers["cache-control"] == "no-store"
        assert len(result.json()["result"]["text"]) >= 50


def test_logout_before_execution_prevents_inference_and_result_delivery(job_api):
    client, jobs, accounts, store, release, entered, calls = job_api
    value = {"mode": "voice", "content": "想找人一起討論餐點與取餐"}
    client.post("/api/v1/ai/post-writing/jobs", headers=accounts[0][1], json=value)
    assert entered.wait(1)
    queued = client.post("/api/v1/ai/post-writing/jobs", headers=accounts[1][1], json=value).json()
    store.logout(accounts[1][1]["Authorization"][7:])
    release.set()
    with jobs.condition:
        assert jobs.condition.wait_for(lambda: jobs.jobs[queued["id"]].finished is not None, 3)
    result = jobs.get(accounts[1][0], queued["id"])
    assert result["status"] == "failed" and result["result"] is None
    assert len(calls) == 1
    assert (
        client.get(
            "/api/v1/ai/post-writing/jobs/" + queued["id"], headers=accounts[1][1]
        ).status_code
        == 401
    )
