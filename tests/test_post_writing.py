import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

from app.api.routes import ai
from app.core_cloud.post_writing import WritingInput, write_post


def provider(text):
    return SimpleNamespace(extract=lambda *_: json.dumps({"text": text}))


def test_draft_preserves_known_numbers_and_uses_structured_output():
    text = "活動剩餘便當，共 5 份，歡迎到正門領取！"
    assert write_post(WritingInput(mode="generate", content=text), provider(text)) == text


@pytest.mark.parametrize(
    "text",
    [
        "每份 80 元！歡迎到正門領取便當。",
        "短稿",
        "無效字元\u0000不能出現在貼文內容裡",
        "無效字元\ud800不能出現在貼文內容裡",
    ],
)
def test_invalid_drafts_fail_without_returning_model_text(text):
    with pytest.raises(HTTPException) as caught:
        write_post(WritingInput(mode="polish", content="活動剩餘便當歡迎領取"), provider(text))
    assert caught.value.detail == "post_writing_invalid_output"


def test_route_local_only_quota_and_rechecks_session(monkeypatch):
    user = SimpleNamespace(id="synthetic-actor")
    config = SimpleNamespace(
        environment="local",
        core_cloud_enabled=False,
        stealth_ai_provider="ollama_local",
        stealth_ollama_local_only_confirmed=True,
        stealth_ollama_model="synthetic-local",
    )
    request = SimpleNamespace(state=SimpleNamespace(runtime_settings=config))
    calls = []
    monkeypatch.setattr(
        ai,
        "OllamaDraftProvider",
        lambda *_args, **_kwargs: (
            calls.append("inference") or provider("活動剩餘便當，歡迎一起領取！")
        ),
    )
    store = SimpleNamespace(authenticate=lambda token: calls.append(token) or user)
    ai.quota.entries.clear()
    value = WritingInput(mode="generate", content="活動剩餘便當歡迎領取")
    assert ai.post_writing(value, request, Response(), store, user, "synthetic-token").text
    assert calls == ["inference", "synthetic-token"]
    store.authenticate = lambda _: SimpleNamespace(id="different-actor")
    with pytest.raises(HTTPException) as caught:
        ai.post_writing(value, request, Response(), store, user, "synthetic-token")
    assert caught.value.status_code == 401
    config.core_cloud_enabled = True
    before = len(calls)
    with pytest.raises(HTTPException) as caught:
        ai.post_writing(value, request, Response(), store, user, "synthetic-token")
    assert caught.value.status_code == 503 and len(calls) == before
    config.core_cloud_enabled = False
    ai.quota.entries.clear()
    for _ in range(6):
        assert ai.quota.allow(("post-writing", str(user.id)), 6)
    with pytest.raises(HTTPException) as caught:
        ai.post_writing(value, request, Response(), store, user, "synthetic-token")
    assert caught.value.status_code == 429
    ai.quota.entries.clear()
