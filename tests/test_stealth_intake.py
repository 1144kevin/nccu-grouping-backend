import json

import httpx
import pytest
from pydantic import ValidationError

from app.core_cloud.intake import IntakeFacts, IntakeRequest, interpret, review
from app.stealth.ollama import LocalModelError, OllamaDraftProvider


def test_missing_facts_are_questions_not_defaults_or_assumed_free():
    result = review(IntakeFacts(title="想找晚餐"), "synthetic:1b")
    assert {q.field for q in result.questions} == {
        "kind",
        "category",
        "location_id",
        "delay_minutes",
        "duration_minutes",
        "budget_max_twd",
        "roles",
    }
    assert not result.ready_for_review
    assert result.facts.known_cost_twd is None


def complete(**changes):
    return IntakeFacts.model_validate(
        {
            "title": "正門一起吃披薩",
            "kind": "need",
            "category": "pizza",
            "location_id": "main_gate",
            "delay_minutes": 60,
            "duration_minutes": 120,
            "budget_max_twd": 120,
            "roles": ["share"],
            "roles_confirmed": True,
            **changes,
        }
    )


def test_budget_is_never_a_quote_and_unknown_is_not_zero():
    result = review(complete(), "synthetic:1b")
    assert result.ready_for_review and result.facts.known_cost_twd is None
    assert review(complete(budget_max_twd=None, budget_unknown=True), "x").ready_for_review
    assert review(complete(budget_max_twd=0, known_cost_twd=0), "x").ready_for_review


@pytest.mark.parametrize("invented_cost", [None, 0, 150])
def test_explicit_unknown_cost_stops_repeated_model_clarification(invented_cost):
    from app.core_cloud.intake import ground

    facts = complete(known_cost_twd=invented_cost, cost_needs_clarification=True)
    ground(facts, "每人預算上限150元；費用未知，不是免費。\n我願意下單。")
    assert facts.known_cost_twd is None
    assert not facts.cost_needs_clarification
    assert facts.evidence.cost == "費用未知"
    assert "cost_basis" not in [q.field for q in review(facts, "synthetic").questions]


@pytest.mark.parametrize("later", ["已確認每人90元", "整團總價540元", "報價每人90元"])
def test_later_price_statement_is_not_erased_by_previous_unknown_cost(later):
    from app.core_cloud.intake import ground

    facts = complete(cost_needs_clarification=True)
    ground(facts, "費用未知。\n" + later)
    assert facts.cost_needs_clarification


def test_negated_unknown_price_is_not_a_declaration():
    from app.core_cloud.intake import ground

    facts = complete(cost_needs_clarification=True)
    ground(facts, "不是費用未知，請確認整團總價。")
    assert facts.cost_needs_clarification


def test_time_role_and_price_conflicts_cannot_pass_review():
    for facts, field in [
        (complete(kind="plan", roles=["share"]), "roles"),
        (complete(kind="offer", roles=[]), "roles"),
        (complete(delay_minutes=10000, duration_minutes=120), "delay_minutes"),
        (complete(known_cost_twd=150), "known_cost_twd"),
    ]:
        result = review(facts, "x")
        assert not result.ready_for_review and field in [q.field for q in result.questions]


@pytest.mark.parametrize(
    "value",
    [
        {"turns": []},
        {"turns": ["x"] * 9},
        {"turns": ["x" * 1001]},
        {"turns": ["x" * 1000] * 5},
        {"turns": [" "]},
        {"turns": ["\0"]},
        {"turns": ["x"], "owner_id": "someone"},
    ],
)
def test_interview_bounds_and_forged_fields(value):
    with pytest.raises(ValidationError):
        IntakeRequest.model_validate(value)


def test_only_user_facts_go_to_verified_loopback_model_no_database_or_secrets():
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "synthetic:1b",
                            "details": {"format": "gguf"},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "content": complete(
                        evidence={
                            "budget": "預算120元",
                            "start": "一小時後",
                            "duration": "持續兩小時",
                            "roles": "願意分食",
                        }
                    ).model_dump_json(),
                },
            },
        )

    provider = OllamaDraftProvider(
        "synthetic:1b", local_only_confirmed=True, transport=httpx.MockTransport(handle)
    )
    result = interpret(
        IntakeRequest(turns=["政大正門找人吃披薩，預算120元，願意分食", "一小時後，持續兩小時"]),
        provider,
    )
    assert result.provider == "ollama_local" and result.ready_for_review
    assert len(requests) == 2
    assert all(r.url.host == "127.0.0.1" and "authorization" not in r.headers for r in requests)
    payload = json.loads(requests[-1].content)
    assert "tools" not in payload and payload["think"] is False
    assert [m["role"] for m in payload["messages"]] == ["system", "user", "user"]
    assert "consent" not in result.model_dump() and "signal_id" not in result.model_dump()


@pytest.mark.parametrize("original", ["我想吃熱湯", "政大東側門，並非政大正門"])
def test_model_cannot_default_to_an_unsupported_or_negated_location(original):
    from app.core_cloud.intake import ground

    facts = complete(title="正門分食需求")
    ground(facts, original)
    assert facts.location_id is None
    assert "正門" not in facts.title
    assert "location_id" in [q.field for q in review(facts, "synthetic").questions]


@pytest.mark.parametrize("title", ["正門熱湯需求", "政大東側門正門熱湯需求"])
def test_summary_location_cannot_contradict_grounded_meeting_location(title):
    from app.core_cloud.intake import ground

    facts = complete(title=title, location_id="east_gate")
    ground(facts, "政大東側門，並非政大正門")
    assert facts.location_id == "east_gate"
    assert facts.title == "政大東側門熱湯需求"


def test_model_cannot_smuggle_consent_or_verified_food_in_json():
    class InvalidProvider:
        model = "synthetic:1b"

        def extract(self, *args):
            return json.dumps(
                {**complete().model_dump(), "agree": True, "dietary_verified": ["保證無過敏原"]}
            )

    with pytest.raises(LocalModelError, match="invalid_local_model_output"):
        interpret(IntakeRequest(turns=["披薩"]), InvalidProvider())
