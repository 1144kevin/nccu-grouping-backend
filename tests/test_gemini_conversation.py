import json
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr, ValidationError

from app.core_cloud import gemini_conversation as module
from app.core_cloud.conversation import ConversationInput, NaturalTurn, converse
from app.core_cloud.intake import IntakeFacts


@pytest.fixture(autouse=True)
def reset_quotas():
    module._quota.entries.clear()
    module._cache.clear()


def answer():
    return NaturalTurn(
        facts=IntakeFacts(title="想喝飲料", category="drink"),
        message="想找人一起喝飲料對吧，現在大概想花多少？",
        question_field="budget_max_twd",
    )


def response(value=None):
    return httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": (value or answer()).model_dump_json()}]},
                }
            ]
        },
    )


def test_one_request_contains_both_sides_and_never_puts_key_in_url_or_body():
    requests = []

    def handle(request):
        requests.append(request)
        return response()

    provider = module.GeminiDialogue(
        SecretStr("test-only-placeholder"), "actor", transport=httpx.MockTransport(handle)
    )
    value = ConversationInput(
        turns=["我想喝飲料", "不想出門"], assistant_turns=["你想自己去拿，還是找人順路帶？"]
    )
    result = provider.respond(value)
    assert result.message == answer().message
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert [m["role"] for m in payload["contents"]] == ["user", "model", "user"]
    assert payload["contents"][1]["parts"][0]["text"] == value.assistant_turns[0]
    assert "test-only-placeholder" not in str(requests[0].url)
    assert "test-only-placeholder" not in requests[0].content.decode()
    assert "tools" not in payload
    assert requests[0].headers["x-goog-api-key"] == "test-only-placeholder"
    provider.respond(value)
    assert len(requests) == 1  # A failed browser acknowledgement need not spend again.


@pytest.mark.parametrize(
    "status,code",
    [
        (429, "stealth_gemini_quota"),
        (403, "stealth_gemini_access_denied"),
        (500, "stealth_gemini_unavailable"),
    ],
)
def test_provider_errors_are_sanitized_and_do_not_retry(status, code):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, text="private provider error body")

    provider = module.GeminiDialogue(
        SecretStr("test-only-placeholder"), "actor", transport=httpx.MockTransport(handle)
    )
    with pytest.raises(HTTPException) as caught:
        provider.respond(ConversationInput(turns=["我想喝飲料"]))
    assert caught.value.detail == code
    assert "private" not in str(caught.value)
    assert len(calls) == 1


def test_missing_key_never_starts_a_request():
    with pytest.raises(HTTPException, match="stealth_gemini_not_configured"):
        module.GeminiDialogue(SecretStr(""), "actor")


def test_a_natural_reply_selects_the_relevant_question_without_writing_anything():
    service, provider = Mock(), Mock()
    provider.respond.return_value = answer()
    result = converse(
        service, ConversationInput(turns=["我想喝飲料，但不知道要花多少"]), dialogue=provider
    )
    assert result.interpretation == "gemini"
    assert result.message == answer().message
    assert result.question.field == "budget_max_twd"
    assert service.mock_calls == []
    provider.respond.assert_called_once()


def test_assistant_suggestions_cannot_become_user_prices_places_or_duties():
    provider = Mock()
    provider.respond.return_value = NaturalTurn(
        facts=IntakeFacts(
            title="飲料需求",
            kind="need",
            category="drink",
            location_id="east_gate",
            budget_max_twd=80,
            roles=["pickup"],
            roles_confirmed=True,
        ),
        message="你比較方便在哪裡碰面？",
        question_field="location_id",
    )
    result = converse(
        Mock(),
        ConversationInput(
            turns=["想喝飲料", "還沒決定"],
            assistant_turns=["預算80元，在東側門，願意取餐，這些是我的建議，不是你的承諾。"],
        ),
        dialogue=provider,
    )
    assert result.facts.budget_max_twd is None
    assert result.facts.location_id is None
    assert result.facts.roles == []


def test_model_cannot_skip_a_cost_conflict_or_start_matching():
    provider = Mock()
    provider.respond.return_value = NaturalTurn(
        facts=IntakeFacts(
            title="披薩優惠", kind="offer", category="pizza", cost_needs_clarification=True
        ),
        message="現在可以先看看確認卡。",
        question_field=None,
    )
    result = converse(
        Mock(), ConversationInput(turns=["我有披薩優惠券，總共300元"]), dialogue=provider
    )
    assert result.signal is None
    assert not result.ready_for_review
    assert result.message != "現在可以先看看確認卡。"
    assert result.question is not None


def test_dialogue_cannot_claim_an_action_already_happened():
    with pytest.raises(ValidationError):
        NaturalTurn(message="已經幫你配對好了", facts=IntakeFacts(title="飲料"))


def test_history_must_align_and_is_bounded():
    with pytest.raises(ValidationError):
        ConversationInput(turns=["飲料"], assistant_turns=["多出的歷史"])
    with pytest.raises(ValidationError):
        ConversationInput(turns=["飲料", "東側門"], assistant_turns=["很長" * 301])


def test_free_tier_guard_limits_repeated_requests_per_actor():
    provider = module.GeminiDialogue(
        SecretStr("test-only-placeholder"),
        "actor",
        transport=httpx.MockTransport(lambda _: response()),
    )
    for index in range(8):
        provider.respond(ConversationInput(turns=[f"我想喝飲料{index}"]))
    with pytest.raises(HTTPException) as caught:
        provider.respond(ConversationInput(turns=["我想喝飲料9"]))
    assert caught.value.status_code == 429


def test_truncated_output_does_not_become_a_partial_matching_request():
    provider = module.GeminiDialogue(
        SecretStr("test-only-placeholder"),
        "actor",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "candidates": [
                        {"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "{}"}]}}
                    ]
                },
            )
        ),
    )
    with pytest.raises(HTTPException, match="stealth_gemini_invalid_output"):
        provider.respond(ConversationInput(turns=["飲料"]))
