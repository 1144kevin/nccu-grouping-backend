import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core_cloud.discovery import discover
from app.core_cloud.match_judge import judge_candidate_pair
from app.core_cloud.stealth import CloudStealth
from app.stealth.ollama import LocalModelError
from app.stealth.provider_contracts import VectorBatch


def _settings(**overrides):
    base = dict(
        stealth_ai_provider="openai",
        environment="local",
        core_cloud_enabled=False,
        local_openai_api_key="sk-test",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _active_signal(title="想買好市多烤雞"):
    return SimpleNamespace(
        id=uuid4(),
        version=1,
        status="active",
        title=title,
        category="shopping",
        tags=["好市多"],
        roles=["buyer"],
        location_id="政大正門",
        starts_at=datetime(2026, 11, 16, 10, 0, tzinfo=UTC),
        ends_at=datetime(2026, 11, 16, 12, 0, tzinfo=UTC),
        budget_max_twd=250,
        participation_preference=None,
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )


class _StubEmbeddings:
    def embed(self, texts, *, purpose):
        return VectorBatch(model_id="synthetic@v1", dimensions=512, vectors=[[1.0] + [0.0] * 511])


def test_judge_candidate_pair_fails_closed_when_unconfigured_or_error_or_invalid_json():
    candidate = {
        "similarity": 0.91,
        "own": {"title": "想買好市多烤雞"},
        "partner": {"title": "有好市多會員可幫忙買"},
    }

    with pytest.raises(LocalModelError, match="openai_not_configured"):
        judge_candidate_pair(
            candidate,
            settings=_settings(stealth_ai_provider="none"),
        )

    class FailingTransport:
        def request(self, path, payload, **kwargs):
            raise LocalModelError("openai_timeout")

    with pytest.raises(LocalModelError, match="openai_timeout"):
        judge_candidate_pair(
            candidate,
            settings=_settings(),
            transport=FailingTransport(),
        )

    class InvalidJsonTransport:
        def request(self, path, payload, **kwargs):
            return {"output_text": "not valid json at all"}

    with pytest.raises(LocalModelError, match="invalid_match_judge_response"):
        judge_candidate_pair(
            candidate,
            settings=_settings(),
            transport=InvalidJsonTransport(),
        )


def test_judge_candidate_pair_distinguishes_verdicts_and_downgrades_empty_reasons_or_conflicts():
    candidate = {
        "similarity": 0.72,
        "own": {"title": "徵好市多會員代買牛奶"},
        "partner": {"title": "有好市多會員可順便代買"},
    }

    class Transport:
        def __init__(self, body):
            self.body = body

        def request(self, path, payload, **kwargs):
            return {"output_text": json.dumps(self.body, ensure_ascii=False)}

    v_ok = judge_candidate_pair(
        candidate,
        settings=_settings(),
        transport=Transport(
            {
                "verdict": "compatible",
                "score": 86,
                "reasons": ["一方需要好市多會員代買，另一方持有會員且可協助購買"],
                "conflicts": [],
                "unknowns": ["取餐時間需再確認"],
            }
        ),
    )
    assert v_ok.verdict == "compatible"
    assert v_ok.score == 86
    assert len(v_ok.reasons) == 1

    v_insufficient = judge_candidate_pair(
        candidate,
        settings=_settings(),
        transport=Transport(
            {
                "verdict": "insufficient_info",
                "score": 35,
                "reasons": [],
                "conflicts": [],
                "unknowns": ["未說明是否願意分攤運費或跑腿費"],
            }
        ),
    )
    assert v_insufficient.verdict == "insufficient_info"

    v_empty_reasons = judge_candidate_pair(
        candidate,
        settings=_settings(),
        transport=Transport(
            {
                "verdict": "compatible",
                "score": 80,
                "reasons": [],
                "conflicts": [],
                "unknowns": [],
            }
        ),
    )
    assert v_empty_reasons.verdict == "incompatible"
    assert v_empty_reasons.score <= 40

    v_with_conflict = judge_candidate_pair(
        candidate,
        settings=_settings(),
        transport=Transport(
            {
                "verdict": "compatible",
                "score": 78,
                "reasons": ["地點相同"],
                "conflicts": ["已知費用超過預算上限"],
                "unknowns": [],
            }
        ),
    )
    assert v_with_conflict.verdict == "incompatible"
    assert v_with_conflict.reasons == []


@pytest.mark.parametrize("verdict_name", ["incompatible", "insufficient_info"])
def test_discover_returns_zero_even_when_only_one_candidate_exists_if_not_compatible(verdict_name):
    signal = _active_signal("徵人合購好市多貝果")
    actions = []

    def request(*args, **kwargs):
        action = kwargs["body"]["action"]
        actions.append(action)
        if action == "index":
            return {"indexed": 1}
        if action == "candidates":
            return {
                "candidates": [
                    {
                        "fingerprint": "fp-single",
                        "own_signal_id": str(signal.id),
                        "candidate_signal_id": "00000000-0000-0000-0000-000000000002",
                        "similarity": 0.93,
                        "own": {"title": "徵人合購好市多貝果", "location_id": "政大正門"},
                        "partner": {"title": "徵人一起去圖書館唸書", "location_id": "政大正門"},
                    }
                ]
            }
        raise AssertionError(f"commit must not be called for {verdict_name}")

    class Transport:
        def request(self, path, payload, **kwargs):
            return {
                "output_text": json.dumps(
                    {
                        "verdict": verdict_name,
                        "score": 15,
                        "reasons": [],
                        "conflicts": ["需求完全不相關"] if verdict_name == "incompatible" else [],
                        "unknowns": ["缺少合作細節"] if verdict_name == "insufficient_info" else [],
                    },
                    ensure_ascii=False,
                )
            }

    settings = _settings()
    service = SimpleNamespace(
        signals=lambda: SimpleNamespace(items=[signal]),
        store=SimpleNamespace(token="tok", settings=settings, _request=request),
    )

    result = discover(service, _StubEmbeddings(), settings=settings, transport=Transport())
    assert result == {
        "created": 0,
        "indexed": 1,
        "is_llm": True,
        "model_id": "synthetic@v1",
        "requires_unanimous_confirmation": True,
    }
    assert actions == ["index", "candidates"]


def test_discover_fails_closed_on_llm_error_and_never_commits():
    signal = _active_signal()
    actions = []

    def request(*args, **kwargs):
        action = kwargs["body"]["action"]
        actions.append(action)
        if action == "index":
            return {"indexed": 1}
        if action == "candidates":
            return {
                "candidates": [
                    {
                        "fingerprint": "fp-1",
                        "own_signal_id": str(signal.id),
                        "candidate_signal_id": "00000000-0000-0000-0000-000000000002",
                        "similarity": 0.90,
                        "own": {"title": "想買好市多烤雞"},
                        "partner": {"title": "有好市多會員可代買"},
                    }
                ]
            }
        raise AssertionError("commit must never be called when LLM fails")

    class FailingTransport:
        def request(self, path, payload, **kwargs):
            raise LocalModelError("openai_rate_limited")

    settings = _settings()
    service = SimpleNamespace(
        signals=lambda: SimpleNamespace(items=[signal]),
        store=SimpleNamespace(token="tok", settings=settings, _request=request),
    )

    with pytest.raises(LocalModelError, match="openai_rate_limited"):
        discover(service, _StubEmbeddings(), settings=settings, transport=FailingTransport())
    assert actions == ["index", "candidates"]


def test_discover_filters_qualified_candidates_before_ranking_and_commits_best():
    signal = _active_signal("徵飲料拼單免運")
    committed_payloads = []

    def request(*args, **kwargs):
        action = kwargs["body"]["action"]
        if action == "index":
            return {"indexed": 1}
        if action == "candidates":
            return {
                "candidates": [
                    {
                        "fingerprint": "fp-high-sim-bad",
                        "own_signal_id": str(signal.id),
                        "candidate_signal_id": "00000000-0000-0000-0000-000000000011",
                        "similarity": 0.97,
                        "own": {"title": "徵飲料拼單免運"},
                        "partner": {"title": "徵人借物理課本"},
                    },
                    {
                        "fingerprint": "fp-lower-sim-good",
                        "own_signal_id": str(signal.id),
                        "candidate_signal_id": "00000000-0000-0000-0000-000000000022",
                        "similarity": 0.65,
                        "own": {"title": "徵飲料拼單免運"},
                        "partner": {"title": "差一杯湊飲料外送免運"},
                    },
                    {
                        "fingerprint": "fp-mid-sim-best",
                        "own_signal_id": str(signal.id),
                        "candidate_signal_id": "00000000-0000-0000-0000-000000000033",
                        "similarity": 0.78,
                        "own": {"title": "徵飲料拼單免運"},
                        "partner": {"title": "同一家手搖飲，我可順路取餐並湊免運"},
                    },
                ]
            }
        if action == "commit":
            committed_payloads.append(kwargs["body"]["args"])
            return {"created": 1}
        raise AssertionError(f"unexpected action {action}")

    class RankingTransport:
        def request(self, path, payload, **kwargs):
            user_text = payload["input"][1]["content"]
            if "物理課本" in user_text:
                body = {
                    "verdict": "incompatible",
                    "score": 10,
                    "reasons": [],
                    "conflicts": ["一個要拼單飲料，一個要借課本，無具體合作價值"],
                    "unknowns": [],
                }
            elif "順路取餐" in user_text:
                body = {
                    "verdict": "compatible",
                    "score": 94,
                    "reasons": ["同一家手搖飲可湊免運", "對方願意順路取餐形成角色互補"],
                    "conflicts": [],
                    "unknowns": ["取餐時間需確認"],
                }
            else:
                body = {
                    "verdict": "compatible",
                    "score": 76,
                    "reasons": ["同為飲料拼單可湊免運"],
                    "conflicts": [],
                    "unknowns": [],
                }
            return {"output_text": json.dumps(body, ensure_ascii=False)}

    settings = _settings()
    service = SimpleNamespace(
        signals=lambda: SimpleNamespace(items=[signal]),
        store=SimpleNamespace(token="tok", settings=settings, _request=request),
    )

    result = discover(service, _StubEmbeddings(), settings=settings, transport=RankingTransport())
    assert result == {
        "created": 1,
        "indexed": 1,
        "is_llm": True,
        "model_id": "synthetic@v1",
        "requires_unanimous_confirmation": True,
    }
    assert len(committed_payloads) == 1
    committed = committed_payloads[0]
    assert committed["candidate_signal_id"] == "00000000-0000-0000-0000-000000000033"
    assert committed["score"] == 94
    assert committed["is_llm"] is True
    assert committed["reasons"] == [
        "同一家手搖飲可湊免運",
        "對方願意順路取餐形成角色互補",
    ]


def test_cloud_stealth_run_returns_is_llm_true(monkeypatch):
    monkeypatch.setattr(
        "app.stealth.provider_registry.local_semantic_matcher",
        lambda settings: SimpleNamespace(embeddings=_StubEmbeddings()),
    )
    monkeypatch.setattr(
        "app.core_cloud.discovery.discover",
        lambda service, embeddings, maximum=1, *, settings=None: {"created": 0, "is_llm": True},
    )
    stealth = CloudStealth(SimpleNamespace(settings=_settings()))
    run = stealth.run(1)
    assert run.created == 0
    assert run.is_llm is True
