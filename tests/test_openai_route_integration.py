"""Synthetic HTTP route test: no Supabase or OpenAI network access."""

import json
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core_cloud.conversation import NaturalTurn
from app.core_cloud.intake import IntakeFacts
from app.core_cloud.openai_budget import LocalBudget
from app.core_cloud.openai_provider import OpenAIDialogue
from app.core_cloud.store import SupabaseCoreStore
from app.core_local import dependencies
from app.core_local.schemas import ProfileView
from app.main import create_app


def test_authenticated_conversation_uses_openai_and_never_writes_database(monkeypatch, tmp_path):
    requests = []
    answer = NaturalTurn(
        facts=IntakeFacts(title="想喝飲料", category="drink"),
        message="想找人一起喝飲料，預算大約多少呢？",
        question_field="budget_max_twd",
    )

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": answer.model_dump_json()}],
                    }
                ],
            },
        )

    monkeypatch.setattr(
        "app.api.routes.stealth_cloud.OpenAIDialogue",
        lambda key, actor, **_kwargs: OpenAIDialogue(
            key,
            actor,
            transport=httpx.MockTransport(handle),
            budget=LocalBudget(tmp_path / "budget.db"),
        ),
    )
    settings = Settings(
        _env_file=None,
        environment="local",
        core_supabase_enabled=True,
        core_local_enabled=False,
        stealth_conversation_provider="openai",
        local_openai_api_key="synthetic",
    )
    monkeypatch.setattr("app.api.routes.stealth_cloud.get_settings", lambda: settings)
    app = create_app(settings)
    store = Mock(spec=SupabaseCoreStore)
    store.token = "synthetic-user-access"
    store.authenticate.return_value = ProfileView(
        id=uuid4(),
        email="fixture@example.test",
        display_name="測試",
        created_at=datetime.now(UTC),
        auth_mode="supabase",
    )
    app.dependency_overrides[dependencies.get_store] = lambda: store
    with TestClient(app) as client:
        url = "/api/v1/stealth-cloud/conversation"
        assert client.post(url, json={"turns": ["我想喝飲料"]}).status_code == 401
        response = client.post(
            url,
            json={"turns": ["我想喝飲料"]},
            headers={"Authorization": "Bearer synthetic-user-access"},
        )
    assert response.status_code == 200
    assert response.json()["interpretation"] == "openai"
    assert response.headers["cache-control"] == "no-store"
    assert requests[0]["store"] is False
    store._request.assert_not_called()
