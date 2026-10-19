import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import Settings
from app.core_cloud.semantic import semantic_cloud_matches
from app.core_cloud.stealth import CloudMatch
from app.stealth.local_providers import OllamaCandidateReviewer, OllamaEmbeddingProvider
from app.stealth.ollama import LocalModelError
from app.stealth.provider_contracts import CandidateContext, CandidateJudgment, VectorBatch
from app.stealth.provider_registry import local_semantic_matcher
from app.stealth.semantic import SemanticMatcher
from app.stealth.semantic_local import run_local_semantic, semantic_candidates
from app.stealth.service import StealthService
from app.stealth.storage import LocalStore
from tests.test_stealth import NOW, setup_group
from tests.test_stealth_matching_matrix import agents_for, make_signal
from tests.test_stealth_planner import match_value


class FakeEmbeddings:
    def embed(self, texts, *, purpose):
        vectors = (
            [[1.0] + [0.0] * 15]
            if purpose == "query"
            else [[0.0, 1.0] + [0.0] * 14, [1.0] + [0.0] * 15][: len(texts)]
        )
        if len(texts) > 2:
            vectors = [[1.0] + [0.0] * 15 for _ in texts]
        return VectorBatch(model_id="synthetic-v1", dimensions=16, vectors=vectors)


def contexts():
    return [
        CandidateContext(key="first", document="冷沙拉", facts=["category", "cost_unknown"]),
        CandidateContext(key="second", document="熱湯", facts=["category", "pickup_volunteer"]),
    ]


def test_semantic_order_is_real_cosine_not_a_probability_or_consent():
    result = SemanticMatcher(FakeEmbeddings()).rank("熱湯", contexts(), review_limit=0)
    assert [item.key for item in result.items] == ["second", "first"]
    assert result.items[0].similarity == 1 and result.requires_unanimous_confirmation is True
    assert "consent" not in result.model_dump() and "price" not in result.model_dump()


@pytest.mark.parametrize(
    "vector", [[0.0] * 16, [float("nan")] * 16, [float("inf")] * 16, [1e300] * 16, [1.0] * 15]
)
def test_invalid_vectors_are_rejected(vector):
    with pytest.raises(ValidationError):
        VectorBatch(model_id="test", dimensions=16, vectors=[vector])


def test_unavailable_model_falls_back_without_faking_semantics():
    class Unavailable:
        def embed(self, *args, **kwargs):
            raise LocalModelError("local_model_busy")

    result = SemanticMatcher(Unavailable()).rank("熱湯", contexts())
    assert result.status == "unavailable" and [i.key for i in result.items] == ["first", "second"]
    assert all(i.similarity is None for i in result.items)


def test_model_version_change_cannot_mix_embedding_spaces():
    class Changed(FakeEmbeddings):
        def embed(self, texts, *, purpose):
            result = super().embed(texts, purpose=purpose)
            if purpose == "document":
                result.model_id = "another-model"
            return result

    result = SemanticMatcher(Changed()).rank("熱湯", contexts())
    assert result.status == "unavailable" and result.warnings == ["embedding_space_changed"]


def test_invented_reasons_are_not_displayed_and_unknowns_stay_uncertain():
    class Reviewer:
        model = "synthetic"

        def review(self, context):
            return CandidateJudgment(verdict="complementary", reason_codes=["offer"])

    result = SemanticMatcher(FakeEmbeddings(), Reviewer()).rank("熱湯", contexts())
    assert all(i.review_status == "unavailable" for i in result.items)
    assert all(i.review is None for i in result.items)
    Reviewer.review = lambda self, c: CandidateJudgment(
        verdict="complementary", reason_codes=["category"]
    )
    result = SemanticMatcher(FakeEmbeddings(), Reviewer()).rank("熱湯", contexts())
    assert next(i for i in result.items if i.key == "first").review.verdict == "uncertain"


@pytest.mark.parametrize(
    "constraint", ["blocked", "budget", "time", "location", "category", "disabled", "dietary"]
)
def test_hard_rejections_never_reach_an_embedding_provider(constraint):
    a, b = make_signal(), make_signal("offer")
    agents = agents_for([a, b])
    blocks = set()
    if constraint == "blocked":
        blocks = {frozenset((a.owner_id, b.owner_id))}
    if constraint == "budget":
        b.known_cost_twd = 1000
    if constraint == "time":
        b.starts_at = a.ends_at
    if constraint == "location":
        b.location_id = "south_gate"
    if constraint == "category":
        b.category = "drink"
    if constraint == "disabled":
        agents[b.owner_id].enabled = False
    if constraint == "dietary":
        a.dietary_requirements = ["不含花生"]

    class Forbidden:
        def embed(self, *args, **kwargs):
            raise AssertionError("Ineligible candidate reached model")

    assert (
        semantic_candidates(
            a.owner_id, [a, b], agents, blocks, NOW, SemanticMatcher(Forbidden())
        ).status
        == "empty"
    )


def test_local_semantic_write_revalidates_blocks_after_inference(tmp_path, monkeypatch):
    service = StealthService(LocalStore(tmp_path / "semantic.sqlite3"))
    users, _ = setup_group(service, ("need", "offer"))
    monkeypatch.setattr("app.stealth.service.utc_now", lambda: NOW)

    class BlockDuringInference(FakeEmbeddings):
        def embed(self, texts, *, purpose):
            if purpose == "query":
                service.block_user(users[0], users[1], NOW)
            return super().embed(texts, purpose=purpose)

    result = run_local_semantic(service, users[0], 3, SemanticMatcher(BlockDuringInference()))
    assert result.created == 0
    assert result.ranking.items == []
    with service.store.transaction() as unit:
        assert unit.all("proposals") == []


def test_cloud_rechecks_visibility_and_never_fetches_private_signal_text():
    first = CloudMatch.model_validate(match_value())
    calls = []

    def matches():
        calls.append(1)
        return SimpleNamespace(items=[first] if len(calls) == 1 else [])

    service = SimpleNamespace(matches=matches)
    result = semantic_cloud_matches(service, SemanticMatcher(FakeEmbeddings()), review_limit=0)
    assert result.items == [] and result.ranking.items == [] and len(calls) == 2


def test_cloud_model_sees_only_own_query_and_aggregate_candidate_summary():
    match = CloudMatch.model_validate(match_value())
    captured = []

    class Capture(FakeEmbeddings):
        def embed(self, texts, *, purpose):
            captured.append((purpose, texts))
            return super().embed(texts, purpose=purpose)

    result = semantic_cloud_matches(
        SimpleNamespace(matches=lambda: SimpleNamespace(items=[match])),
        SemanticMatcher(Capture()),
        review_limit=0,
    )
    assert len(result.items) == 1
    assert captured[0] == ("query", [match.my_signal_title])
    assert match.my_signal_title not in captured[1][1][0]
    assert "取餐" in captured[1][1][0] and str(match.id) not in captured[1][1][0]
    assert result.items[0].terms is None and result.items[0].confirmed_count == 0


def test_cloud_revision_change_discards_stale_ranking():
    initial = CloudMatch.model_validate(match_value())
    revised = initial.model_copy(update={"terms_revision": 1})
    calls = iter([initial, revised])
    result = semantic_cloud_matches(
        SimpleNamespace(matches=lambda: SimpleNamespace(items=[next(calls)])),
        SemanticMatcher(FakeEmbeddings()),
        review_limit=0,
    )
    assert result.items == [] and result.ranking.warnings


def test_embedding_adapter_blocks_remote_alias_before_embed():
    requests = []

    def handle(request):
        requests.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "qwen3-embedding:0.6b",
                        "remote_host": "cloud.example",
                        "details": {"format": "gguf"},
                    }
                ]
            },
        )

    provider = OllamaEmbeddingProvider(
        "qwen3-embedding:0.6b", local_only_confirmed=True, transport=httpx.MockTransport(handle)
    )
    with pytest.raises(LocalModelError, match="cloud_model_not_allowed"):
        provider.embed(["合成測試"], purpose="document")
    assert requests == ["/api/tags"]


def test_candidate_reviewer_refuses_model_injected_actions():
    provider = SimpleNamespace(
        model="synthetic",
        extract=lambda *args: json.dumps(
            {"verdict": "complementary", "reason_codes": ["category"], "agree": True}
        ),
    )
    with pytest.raises(LocalModelError, match="invalid_candidate_review"):
        OllamaCandidateReviewer(provider).review(contexts()[0])


def test_candidate_output_schema_only_allows_verified_fact_codes():
    captured = []

    def extract(messages, schema, prompt):
        captured.append(schema)
        return json.dumps({"verdict": "complementary", "reason_codes": ["category"]})

    provider = SimpleNamespace(model="synthetic", extract=extract)
    context = CandidateContext(
        key="isolated", document="正餐，兩人協調", facts=["category", "coupon_terms"]
    )
    result = OllamaCandidateReviewer(provider).review(context)
    assert result.verdict == "uncertain"  # Coupon conditions still need human confirmation.
    reasons = captured[0]["properties"]["reason_codes"]
    assert reasons["items"]["enum"] == ["category", "coupon_terms"]
    assert reasons["minItems"] == 1
    assert CandidateJudgment.model_json_schema()["properties"]["reason_codes"]["items"]["enum"] != [
        "category",
        "coupon_terms",
    ]


def test_candidate_reviewer_still_refuses_codes_outside_constrained_schema():
    provider = SimpleNamespace(
        model="synthetic",
        extract=lambda *args: json.dumps(
            {"verdict": "uncertain", "reason_codes": ["cost_unknown"]}
        ),
    )
    with pytest.raises(LocalModelError, match="ungrounded_candidate_review"):
        OllamaCandidateReviewer(provider).review(
            CandidateContext(key="isolated", document="已確認價格", facts=["category"])
        )


def test_reviewer_receives_requester_query_without_granting_actions():
    seen = []

    class Reviewer:
        model = "synthetic"

        def review(self, context):
            seen.append(context.query)
            return CandidateJudgment(verdict="similar", reason_codes=["category"])

    SemanticMatcher(FakeEmbeddings(), Reviewer()).rank("想喝熱湯", contexts())
    assert seen == ["想喝熱湯", "想喝熱湯"]


@pytest.mark.parametrize("changes", [{}, {"core_cloud_enabled": True}, {"environment": "cloud"}])
def test_registry_disabled_or_cloud_never_constructs_models(changes, monkeypatch):
    settings = dict(_env_file=None, stealth_ollama_local_only_confirmed=True)
    if changes:
        settings["stealth_semantic_enabled"] = True
    settings.update(changes)

    def forbidden(*args, **kwargs):
        raise AssertionError("must reject before constructing a provider")

    monkeypatch.setattr("app.stealth.provider_registry.OllamaEmbeddingProvider", forbidden)
    with pytest.raises(HTTPException, match="local_semantic_not_configured"):
        local_semantic_matcher(Settings(**settings))
