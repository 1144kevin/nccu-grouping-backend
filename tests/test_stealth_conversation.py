from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core_cloud.conversation import ConversationInput, contextual_turn, converse
from app.core_cloud.intake import IntakeFacts, review

NOW = datetime(2026, 10, 29, tzinfo=UTC)


def test_all_quick_replies_complete_a_chat_without_inference_or_writes():
    service, interpreter = Mock(), Mock()
    turns, fields, previous = ["我想找人一起喝飲料"], [None], None
    answers = {
        "location_id": "取餐地點在政大正門",
        "delay_minutes": "從現在30分鐘後開始",
        "duration_minutes": "可以配合60分鐘",
        "budget_max_twd": "80",
        "roles": "我暫不負責任何分工",
    }
    for _ in range(7):
        result = converse(
            service,
            ConversationInput(turns=turns, answer_fields=fields, previous=previous),
            interpreter=interpreter,
            now=NOW,
        )
        assert result.interpretation == "quick_answer"
        if result.ready_for_review:
            break
        assert result.question is not None
        turns.append(answers[result.question.field])
        fields.append(result.question.field)
        previous = result.facts
    assert result.ready_for_review
    assert result.signal.budget_max_twd == 80
    assert result.signal.known_cost_twd is None
    assert result.signal.roles == []
    assert result.signal.location_id == "main_gate"
    assert result.facts.roles_confirmed
    interpreter.interpret.assert_not_called()
    assert service.mock_calls == []


def test_multi_field_free_text_skips_answered_questions_and_does_not_use_history():
    service, interpreter = Mock(), Mock()
    facts = IntakeFacts(
        title="想喝50嵐",
        category="drink",
        location_id="east_gate",
        delay_minutes=30,
        budget_max_twd=80,
    )
    interpreter.interpret.return_value = review(facts, "test")
    result = converse(
        service,
        ConversationInput(turns=["想喝50嵐，東側門，半小時後，預算80"]),
        interpreter=interpreter,
        now=NOW,
    )
    assert result.question.field == "duration_minutes"
    assert result.signal.location_id == "east_gate"
    assert result.signal.budget_max_twd == 80
    interpreter.interpret.assert_called_once()
    assert service.mock_calls == []


def test_defaults_remain_visible_proposals_and_do_not_hide_missing_fields():
    service = Mock()
    result = converse(service, ConversationInput(turns=["我想找人一起喝飲料"]), now=NOW)
    assert result.question.field == "location_id"
    assert result.facts.location_id is None and result.facts.delay_minutes is None
    assert result.signal.location_id == "main_gate"
    assert result.signal.budget_max_twd is None
    assert any("首次建議" in source for source in result.sources)
    assert not result.ready_for_review
    assert service.mock_calls == []


def test_budget_only_statement_does_not_trigger_a_price_basis_question():
    interpreter = Mock()
    interpreter.interpret.return_value = review(
        IntakeFacts(
            title="我有披薩優惠券",
            category="pizza",
            kind="offer",
            roles=["coupon"],
            budget_max_twd=150,
            cost_needs_clarification=True,
        ),
        "test",
    )
    result = converse(
        Mock(),
        ConversationInput(turns=["我有披薩優惠券，預算150元"]),
        interpreter=interpreter,
        now=NOW,
    )
    assert result.question.field == "location_id"
    assert result.signal.known_cost_twd is None


def test_coupon_starter_keeps_its_explicit_role_after_selecting_food():
    first = converse(Mock(), ConversationInput(turns=["我有優惠券想分享"]), now=NOW)
    assert first.question.field == "category"
    result = converse(
        Mock(),
        ConversationInput(
            turns=["我有優惠券想分享", "改成披薩，想找人一起吃"],
            answer_fields=[None, "category"],
            previous=first.facts,
        ),
        now=NOW,
    )
    assert "coupon" in result.facts.roles
    assert result.question.field == "location_id"
    assert result.signal.kind == "offer"


def test_a_short_budget_answer_uses_its_question_but_never_becomes_a_price():
    assert contextual_turn("80", "budget_max_twd") == "我的預算上限是80元"
    assert contextual_turn("80", "cost_basis") == "每人費用是80元"
    assert contextual_turn("80", None) == "80"
    with pytest.raises(HTTPException):
        contextual_turn("999999", "budget_max_twd")


def test_client_previous_prices_without_evidence_are_not_kept_by_a_quick_answer():
    previous = IntakeFacts(
        title="想喝飲料",
        category="drink",
        budget_max_twd=900,
        known_cost_twd=800,
        roles=["pickup"],
        roles_confirmed=True,
    )
    result = converse(
        Mock(),
        ConversationInput(
            turns=["我想找人一起喝飲料", "取餐地點在政大正門"],
            answer_fields=[None, "location_id"],
            previous=previous,
        ),
        now=NOW,
    )
    assert result.facts.budget_max_twd is None and result.facts.known_cost_twd is None
    assert result.facts.roles == []


def test_corrections_use_one_inference_and_keep_all_user_turns():
    interpreter = Mock()
    interpreter.interpret.return_value = review(
        IntakeFacts(title="改吃披薩", category="pizza", budget_max_twd=70), "test"
    )
    result = converse(
        Mock(),
        ConversationInput(
            turns=["我想喝50嵐", "改成披薩，預算改成70元"], answer_fields=[None, "location_id"]
        ),
        interpreter=interpreter,
        now=NOW,
    )
    assert result.signal.category == "pizza" and result.signal.budget_max_twd == 70
    assert not any(t.startswith("品牌:") for t in result.signal.tags)
    assert interpreter.interpret.call_args.args[0].turns[-1] == "改成披薩，預算改成70元"


def test_inconsistent_money_and_resource_roles_cannot_be_confirmed_early():
    interpreter = Mock()
    for facts in (
        IntakeFacts(title="披薩", category="pizza", known_cost_twd=100, budget_max_twd=80),
        IntakeFacts(title="我有披薩", category="pizza", kind="offer", roles=[]),
        IntakeFacts(title="披薩", category="pizza", cost_needs_clarification=True),
    ):
        interpreter.interpret.return_value = review(facts, "test")
        result = converse(
            Mock(), ConversationInput(turns=[facts.title]), interpreter=interpreter, now=NOW
        )
        assert result.signal is None and result.question is not None


def test_context_is_bounded_and_model_outage_never_discards_followups():
    with pytest.raises(ValidationError):
        ConversationInput(turns=["喝飲料"], answer_fields=[None, "location_id"])
    with pytest.raises(ValidationError):
        ConversationInput(turns=["x"] * 17)
    with pytest.raises(HTTPException) as error:
        converse(Mock(), ConversationInput(turns=["想喝飲料", "改到東側門"]))
    assert error.value.detail == "quick_interview_unavailable"


def test_clock_window_and_custom_dorm_location_are_preserved_without_redundant_delay_question():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from app.core_cloud.conversation import NaturalTurn

    # 14:24 Taipei == 06:24 UTC
    taipei_now = datetime(2026, 11, 13, 14, 24, tzinfo=ZoneInfo("Asia/Taipei")).astimezone(
        timezone.utc
    )
    dialogue = Mock()
    dialogue.provider = "openai"
    dialogue.respond.return_value = NaturalTurn(
        message="你希望從現在起大約多久後開始約？",
        question_field="delay_minutes",
        facts=IntakeFacts(
            title="找人一起吃麥當勞薯條和漢堡",
            kind="need",
            category="meal",
            location_id="政大自強十舍門口",
            delay_minutes=None,
            duration_minutes=None,
            budget_max_twd=200,
        ),
    )
    result = converse(
        Mock(),
        ConversationInput(
            turns=[
                "想找人一起吃麥當勞薯條和漢堡",
                "今天晚上6點到8點之間都可以",
                "我現在在政大自強十舍，希望可以在宿舍門口拿餐",
                "我晚點預算最多200，如果有人幫忙跑腿，我可以另外補貼30",
            ],
            answer_fields=[None, None, None, None],
        ),
        dialogue=dialogue,
        now=taipei_now,
    )
    assert result.facts.location_id == "政大自強十舍"
    assert result.facts.delay_minutes == 216
    assert result.facts.duration_minutes == 120
    assert result.question is None or result.question.field != "delay_minutes"
    assert result.signal is not None
    assert result.signal.location_id == "政大自強十舍"
    assert result.signal.starts_at == datetime(2026, 11, 13, 18, 0, tzinfo=ZoneInfo("Asia/Taipei")).astimezone(timezone.utc)
    assert result.signal.ends_at == datetime(2026, 11, 13, 20, 0, tzinfo=ZoneInfo("Asia/Taipei")).astimezone(timezone.utc)


def test_tsundere_tone_preference_is_accepted():
    from app.core_cloud.conversation import ConversationPreferences

    prefs = ConversationPreferences(tone="tsundere", context="喜歡喝無糖茶")
    assert prefs.tone == "tsundere"

