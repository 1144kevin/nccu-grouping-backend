import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core_cloud.stealth import CloudAgentInput
from app.core_cloud.store import SupabaseCoreStore
from app.core_local import dependencies
from app.core_local.schemas import ProfileView
from app.main import app
from app.stealth.schemas import SignalInput


@pytest.fixture
def cloud_api(monkeypatch):
    monkeypatch.setenv("CORE_SUPABASE_ENABLED", "true")
    monkeypatch.setenv("CORE_LOCAL_ENABLED", "false")
    monkeypatch.setenv("ENVIRONMENT", "local")
    get_settings.cache_clear()
    store = Mock(spec=SupabaseCoreStore)
    store.token = "synthetic-user-access"
    user = ProfileView(
        id=uuid4(),
        email="fixture@example.test",
        display_name="測試",
        created_at=datetime.now(UTC),
        auth_mode="supabase",
    )
    store.authenticate.return_value = user
    app.dependency_overrides[dependencies.get_store] = lambda: store
    try:
        with TestClient(app) as client:
            yield client, store, user
    finally:
        app.dependency_overrides.pop(dependencies.get_store, None)
        get_settings.cache_clear()


HEADERS = {"Authorization": "Bearer synthetic-user-access"}
ROOT = "/api/v1/stealth-cloud"


def test_intake_requires_auth_and_explicit_local_configuration_without_writing(cloud_api):
    client, store, _ = cloud_api
    assert client.post(ROOT + "/intake", json={"turns": ["披薩"]}).status_code == 401
    response = client.post(ROOT + "/intake", json={"turns": ["披薩"]}, headers=HEADERS)
    assert response.status_code == 503
    assert response.json()["detail"] == "local_model_not_configured"
    store._request.assert_not_called()


def test_semantic_requires_auth_and_local_opt_in_before_any_database_call(cloud_api):
    from app.core.config import Settings

    client, store, _ = cloud_api
    store.settings = Settings(_env_file=None)
    assert client.post(ROOT + "/matches/semantic", json={}).status_code == 401
    response = client.post(ROOT + "/matches/semantic", json={}, headers=HEADERS)
    assert response.status_code == 503
    assert response.json()["detail"] == "local_semantic_not_configured"
    store._request.assert_not_called()


def test_intake_uses_configured_model_and_never_writes_on_success(cloud_api, monkeypatch):
    from app.core_cloud.intake import IntakeFacts, review

    client, store, _ = cloud_api
    monkeypatch.setenv("STEALTH_AI_PROVIDER", "ollama_local")
    monkeypatch.setenv("STEALTH_OLLAMA_MODEL", "synthetic:1b")
    monkeypatch.setenv("STEALTH_OLLAMA_LOCAL_ONLY_CONFIRMED", "true")
    get_settings.cache_clear()
    calls = []

    def extract(value, provider):
        calls.append((value.turns, provider.model))
        return review(IntakeFacts(title="披薩需求"), provider.model)

    monkeypatch.setattr("app.stealth.local_providers.interpret", extract)
    result = client.post(ROOT + "/intake", json={"turns": ["想吃披薩"]}, headers=HEADERS)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert result.json()["is_llm"] is True and not result.json()["ready_for_review"]
    assert calls == [(["想吃披薩"], "synthetic:1b")]
    store._request.assert_not_called()


def signal_value():
    now = datetime.now(UTC)
    return SignalInput(
        kind="need",
        category="meal",
        title="私人需求測試",
        location_id="main_gate",
        starts_at=now + timedelta(hours=1),
        ends_at=now + timedelta(hours=3),
        expires_at=now + timedelta(hours=3),
        budget_max_twd=0,
        tags=["晚餐"],
    ).model_dump(mode="json")


def test_cloud_requires_existing_authenticated_account(cloud_api):
    client, store, _ = cloud_api
    assert client.get(ROOT + "/agent").status_code == 401
    store._request.assert_not_called()
    store._request.return_value = None
    result = client.get(ROOT + "/agent", headers=HEADERS)
    assert result.status_code == 200 and result.json() is None
    assert result.headers["cache-control"] == "no-store"
    assert store._request.call_args.kwargs == {
        "body": {"action": "agent_get", "args": {}},
        "token": store.token,
    }


def test_agent_defaults_are_opted_out_and_owner_cannot_be_forged(cloud_api):
    client, store, user = cloud_api
    value = CloudAgentInput().model_dump(mode="json")
    store._request.return_value = {
        **CloudAgentInput().model_dump(mode="json", exclude={"expected_version"}),
        "user_id": str(user.id),
        "version": 1,
        "policy_version": 1,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    result = client.put(ROOT + "/agent", json=value, headers=HEADERS)
    assert (
        result.status_code == 200
        and not result.json()["enabled"]
        and not result.json()["include_demo"]
    )
    store._request.reset_mock()
    result = client.put(ROOT + "/agent", json={**value, "user_id": str(uuid4())}, headers=HEADERS)
    assert result.status_code == 422
    store._request.assert_not_called()


def test_signal_is_saved_even_when_match_run_is_in_cooldown(cloud_api):
    client, store, user = cloud_api
    value = signal_value()
    signal = {
        **value,
        "id": str(uuid4()),
        "owner_id": str(user.id),
        "created_at": datetime.now(UTC).isoformat(),
        "status": "active",
        "version": 1,
    }
    store._request.side_effect = [signal, HTTPException(429, "match_cooldown")]
    key = "post-synthetic-idempotency-key"
    result = client.post(ROOT + "/signals", json=value, headers={**HEADERS, "Idempotency-Key": key})
    assert result.status_code == 201 and result.json()["matching"] == "cooldown"
    assert result.json()["signal"]["budget_max_twd"] == 0
    first = store._request.call_args_list[0].kwargs["body"]
    assert first["args"]["request_key"] == str(uuid5(NAMESPACE_URL, "core-stealth:" + key))
    assert "owner_id" not in first["args"]["value"]


@pytest.mark.parametrize("invalid", ["hidden\0text", "hidden\ud800text"])
def test_invalid_text_does_not_reach_supabase(cloud_api, invalid):
    client, store, _ = cloud_api
    value = {**signal_value(), "title": invalid}
    result = client.post(
        ROOT + "/signals",
        content=json.dumps(value),
        headers={**HEADERS, "Content-Type": "application/json", "Idempotency-Key": str(uuid4())},
    )
    assert result.status_code == 422 and "hidden" not in result.text
    store._request.assert_not_called()


def test_draft_is_offline_and_does_not_write_or_call_a_model(cloud_api):
    client, store, _ = cloud_api
    result = client.post(ROOT + "/draft", json={"text": "一小時後想在正門吃披薩"}, headers=HEADERS)
    assert result.status_code == 200 and result.json()["is_llm"] is False
    assert result.json()["category_hint"] == "pizza"
    store._request.assert_not_called()


def test_match_bounds_and_decision_actor_are_validated_before_rpc(cloud_api):
    client, store, _ = cloud_api
    assert (
        client.post(ROOT + "/matches/run", json={"max_proposals": 6}, headers=HEADERS).status_code
        == 422
    )
    assert (
        client.post(
            ROOT + f"/matches/{uuid4()}/decision",
            json={"decision": "accepted", "actor": str(uuid4())},
            headers=HEADERS,
        ).status_code
        == 422
    )
    assert client.delete(ROOT + "/signals/not-a-uuid", headers=HEADERS).status_code == 422
    store._request.assert_not_called()


@pytest.mark.parametrize("agree", [False, 1, "true", None])
def test_formation_requires_literal_user_consent(cloud_api, agree):
    client, store, _ = cloud_api
    response = client.post(
        ROOT + f"/matches/{uuid4()}/consent",
        json={"expected_revision": 1, "agree": agree},
        headers=HEADERS,
    )
    assert response.status_code == 422
    store._request.assert_not_called()


def test_formation_terms_do_not_accept_forged_host_or_fractional_cost(cloud_api):
    client, store, _ = cloud_api
    value = {
        "content": "大家同意後一起吃披薩。",
        "cost_per_person_twd": 90,
        "deadline_at": datetime.now(UTC).isoformat(),
        "order_member": 1,
        "pickup_member": 2,
    }
    for invalid in (
        {**value, "host_id": str(uuid4())},
        {**value, "cost_per_person_twd": 90.5},
        {**value, "content": "hidden\0text"},
        {**value, "cost_change_note": "hidden\0note"},
        {**value, "cost_change_note": "x" * 121},
    ):
        assert (
            client.put(
                ROOT + f"/matches/{uuid4()}/terms",
                json={"expected_revision": 0, "value": invalid},
                headers=HEADERS,
            ).status_code
            == 422
        )
    store._request.assert_not_called()


def test_formation_uses_current_session_and_revision(cloud_api):
    client, store, _ = cloud_api
    store._request.side_effect = HTTPException(409, "terms_revision_conflict")
    proposal = uuid4()
    response = client.post(
        ROOT + f"/matches/{proposal}/consent",
        json={"expected_revision": 2, "agree": True},
        headers=HEADERS,
    )
    assert response.status_code == 409 and response.json()["detail"] == "terms_revision_conflict"
    assert store._request.call_args.args == ("POST", "/rest/v1/rpc/core_stealth_formation")
    assert store._request.call_args.kwargs["body"]["args"] == {
        "proposal_id": str(proposal),
        "expected_revision": 2,
        "agree": True,
    }
    assert store._request.call_args.kwargs["token"] == store.token


def test_match_list_proposes_draft_without_writes_or_private_context_exposure(cloud_api):
    client, store, _ = cloud_api
    now = datetime.now(UTC)
    value = dict(
        id=str(uuid4()),
        created_at=now.isoformat(),
        category="pizza",
        location_id="main_gate",
        starts_at=(now + timedelta(hours=1)).isoformat(),
        ends_at=(now + timedelta(hours=2)).isoformat(),
        expires_at=(now + timedelta(minutes=30)).isoformat(),
        budget_max_twd=120,
        known_cost_twd=90,
        member_count=2,
        score=75,
        reasons=[],
        unknowns=[],
        my_decision="pending",
        status="pending",
        my_signal_title="我的私人原文",
        my_roles=["share"],
        invitation_text="有互補機會",
        why_me=[],
        planning_members=[
            {"number": 1, "roles": ["share"]},
            {"number": 2, "roles": ["order", "pickup"]},
        ],
    )
    store._request.return_value = {"items": [value]}
    response = client.get(ROOT + "/matches", headers=HEADERS)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    result = response.json()["items"][0]
    assert result["agent_draft"]["cost_per_person_twd"] == 90
    assert result["agent_draft"]["order_member"] == result["agent_draft"]["pickup_member"] == 2
    assert "planning_members" not in result and "私人原文" not in json.dumps(
        result["agent_draft"], ensure_ascii=False
    )
    assert result["terms"] is None and result["terms_revision"] == result["confirmed_count"] == 0
    store._request.assert_called_once()
    assert store._request.call_args.kwargs["body"] == {"action": "matches", "args": {}}
