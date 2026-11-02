from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core_cloud.intake import IntakeFacts, IntakeRequest, review
from app.core_cloud.quick_stealth import QuickStart, interview, prepare, start
from app.core_cloud.stealth import CloudAgent, CloudSignalCreated
from app.stealth.ollama import LocalModelError
from app.stealth.schemas import Signal

NOW = datetime.now(UTC)


def history(**changes):
    value, _, _ = prepare("我想喝50嵐", [], now=NOW)
    return Signal(
        **{
            **value.model_dump(),
            "owner_id": uuid4(),
            "id": uuid4(),
            "created_at": NOW - timedelta(days=2),
            "version": 1,
            "status": "active",
            **changes,
        }
    )


def test_one_sentence_has_a_search_window_but_never_invents_price_or_duties():
    value, sources, method = prepare("我今天想喝50嵐", [], now=NOW)
    assert value.category == "drink" and "品牌:50嵐" in value.tags
    assert value.roles == [] and value.known_cost_twd is None and value.budget_max_twd is None
    assert value.starts_at == NOW and value.ends_at == NOW + timedelta(hours=24)
    assert value.location_id == "main_gate" and "首次建議" in sources[0]
    assert method == "keyword_fallback"


def test_reuses_only_recent_same_category_constraints_not_price_or_duty():
    old = history(location_id="east_gate", budget_max_twd=100, known_cost_twd=70, roles=["pickup"])
    value, sources, _ = prepare("想喝50嵐", [old], now=NOW)
    assert value.location_id == "east_gate" and value.budget_max_twd == 100
    assert value.roles == [] and value.known_cost_twd is None
    assert "近30天" in sources[0]
    value, _, _ = prepare(
        "想喝50嵐", [old.model_copy(update={"created_at": NOW - timedelta(days=40)})], now=NOW
    )
    assert value.location_id == "main_gate" and value.budget_max_twd is None


def test_current_description_overrides_history_and_missing_details_do_not_trigger_an_interview():
    interpreter = Mock()
    interpreter.interpret.return_value = review(
        IntakeFacts(
            title="南門喝50嵐",
            category="drink",
            location_id="south_gate",
            delay_minutes=60,
            duration_minutes=45,
            budget_unknown=True,
        ),
        "synthetic",
    )
    value, _, method = prepare(
        "一小時後在南門喝50嵐", [history(budget_max_twd=100)], interpreter=interpreter, now=NOW
    )
    assert value.location_id == "south_gate" and value.starts_at == NOW + timedelta(hours=1)
    assert value.budget_max_twd is None and method == "local_model"
    assert value.roles == []


def test_model_failure_is_transparent_and_unknown_categories_are_not_guessed():
    interpreter = Mock()
    interpreter.interpret.side_effect = LocalModelError("local_model_busy")
    assert prepare("想喝50嵐", [], interpreter=interpreter, now=NOW)[2] == "keyword_fallback"
    with pytest.raises(HTTPException) as error:
        prepare("我想做點事", [], now=NOW)
    assert error.value.detail == "quick_category_unclear"


def test_retries_recover_original_signal_before_model_or_activation():
    service = Mock()
    signal = history()
    service.store._request.return_value = signal.model_dump(mode="json")
    result = start(service, QuickStart(text=signal.title), "same-key-123456")
    assert result.created.signal.id == signal.id and result.interpretation == "retry"
    service.agent.assert_not_called()
    service.create.assert_not_called()
    with pytest.raises(HTTPException):
        start(service, QuickStart(text="different sentence"), "same-key-123456")


def test_start_requires_explicit_activation_and_uses_existing_create():
    service = Mock()
    service.store._request.return_value = None
    service.agent.return_value = None
    service.signals.return_value = SimpleNamespace(items=[])
    with pytest.raises(HTTPException):
        start(service, QuickStart(text="喝50嵐"), "test-key-123456")
    service.save_agent.assert_not_called()
    service.create.return_value = CloudSignalCreated(signal=history(), matching="completed")
    start(service, QuickStart(text="喝50嵐", activate_agent=True), "test-key-123456")
    agent = service.save_agent.call_args.args[0]
    assert agent.enabled is True and agent.include_demo is False and agent.expected_version == 0
    assert service.create.call_args.args[0].roles == []


def test_existing_preferences_are_not_replaced_when_reactivating():
    service = Mock()
    service.store._request.return_value = None
    service.agent.return_value = CloudAgent(
        user_id=uuid4(),
        version=4,
        updated_at=NOW,
        name="本人的分身",
        enabled=False,
        daily_invite_limit=2,
        quiet_start_hour=22,
        quiet_end_hour=8,
    )
    service.signals.return_value = SimpleNamespace(items=[])
    service.create.return_value = CloudSignalCreated(signal=history(), matching="quiet")
    start(service, QuickStart(text="喝50嵐", activate_agent=True), "test-key-123456")
    value = service.save_agent.call_args.args[0]
    assert (
        value.name == "本人的分身"
        and value.quiet_start_hour == 22
        and value.daily_invite_limit == 2
    )
    assert value.expected_version == 4


def test_optional_interview_does_not_need_all_fields_and_never_activates_or_saves():
    service = Mock()
    service.signals.return_value = SimpleNamespace(items=[])
    interpreter = Mock()
    interpreter.interpret.return_value = review(
        IntakeFacts(title="想喝50嵐", category="drink"), "synthetic"
    )
    result = interview(service, IntakeRequest(turns=["想喝50嵐"]), interpreter=interpreter, now=NOW)
    assert result.signal is not None and result.signal.location_id == "main_gate"
    assert result.signal.ends_at == NOW + timedelta(hours=24)
    assert result.signal.budget_max_twd is None and result.signal.roles == []
    assert result.questions and all(
        q.field in ("location_id", "delay_minutes", "budget_max_twd") for q in result.questions
    )
    service.create.assert_not_called()
    service.save_agent.assert_not_called()
    service.agent.assert_not_called()


def test_followup_uses_all_turns_and_previous_facts_without_filling_a_form():
    service = Mock()
    service.signals.return_value = SimpleNamespace(
        items=[history(location_id="south_gate", budget_max_twd=300)]
    )
    previous = IntakeFacts(title="飲料需求", category="drink", roles=["coupon"])
    interpreter = Mock()
    interpreter.interpret.return_value = review(
        IntakeFacts(
            title="東側門喝迷客夏",
            category="drink",
            location_id="east_gate",
            delay_minutes=30,
            budget_max_twd=120,
            roles=["coupon"],
            kind="offer",
        ),
        "synthetic",
    )
    value = IntakeRequest(
        turns=["我有50嵐優惠券", "改成迷客夏，東側門，30分鐘後，預算120"], previous=previous
    )
    result = interview(service, value, interpreter=interpreter, now=NOW)
    interpreter.interpret.assert_called_once_with(value)
    assert result.signal.location_id == "east_gate" and result.signal.budget_max_twd == 120
    assert result.signal.starts_at == NOW + timedelta(minutes=30)
    assert result.signal.kind == "offer" and result.signal.roles == ["coupon"]
    assert "品牌:迷客夏" in result.signal.tags and "品牌:50嵐" not in result.signal.tags
    assert result.signal.known_cost_twd is None


def test_unknown_category_can_continue_chat_but_cannot_start_a_guessed_food_request():
    service = Mock()
    service.signals.return_value = SimpleNamespace(items=[])
    result = interview(service, IntakeRequest(turns=["我願意幫忙做點事"]), now=NOW)
    assert result.signal is None and result.questions[0].field == "category"
    service.create.assert_not_called()


def test_offline_followups_fail_instead_of_discarding_the_users_corrections():
    service = Mock()
    with pytest.raises(HTTPException) as error:
        interview(service, IntakeRequest(turns=["想吃披薩", "改東側門，預算120"]), now=NOW)
    assert error.value.detail == "quick_interview_unavailable"
    service.signals.assert_not_called()
    service.create.assert_not_called()


def test_confirmed_chat_signal_is_created_exactly_without_a_second_interpretation():
    service = Mock()
    service.store._request.return_value = None
    service.agent.return_value = None
    signal, _, _ = prepare("我有披薩優惠券", [], now=NOW)
    service.create.return_value = CloudSignalCreated(signal=history(), matching="completed")
    interpreter = Mock()
    result = start(
        service,
        QuickStart(text=signal.title, activate_agent=True, confirmed_signal=signal),
        "confirmed-chat-key",
        interpreter=interpreter,
    )
    assert result.interpretation == "reviewed"
    assert service.create.call_args.args[0] == signal
    interpreter.interpret.assert_not_called()
    service.signals.assert_not_called()
    assert service.save_agent.call_args.args[0].enabled


def test_confirmed_retry_rejects_reusing_a_key_with_changed_details():
    service = Mock()
    saved = history()
    signal = prepare(saved.title, [], now=NOW)[0]
    service.store._request.return_value = saved.model_dump(mode="json")
    assert (
        start(
            service, QuickStart(text=saved.title, confirmed_signal=signal), "same-confirmed-key"
        ).interpretation
        == "retry"
    )
    for changed in (
        signal.model_copy(update={"budget_max_twd": 99}),
        signal.model_copy(update={"roles": ["pickup"]}),
    ):
        with pytest.raises(HTTPException) as error:
            start(
                service,
                QuickStart(text=saved.title, confirmed_signal=changed),
                "same-confirmed-key",
            )
        assert error.value.detail == "idempotency_payload_conflict"
    service.create.assert_not_called()


@pytest.mark.parametrize(
    "text,kind,roles",
    [
        ("我有披薩買一送一優惠券", "offer", ["coupon"]),
        ("我願意順路幫忙取餐，想吃披薩", "plan", ["pickup"]),
        ("朋友可以幫忙取餐，我想吃披薩", "need", []),
        ("我願意取餐，但我不方便取餐，我想吃披薩", "need", []),
    ],
)
def test_keyword_fallback_recognizes_only_explicit_personal_resources_and_roles(text, kind, roles):
    signal, _, _ = prepare(text, [], now=NOW)
    assert signal.kind == kind and signal.roles == roles


@pytest.mark.parametrize(
    "text,brand",
    [
        ("先想50嵐\n不要50嵐，改迷客夏", "迷客夏"),
        ("想喝50嵐\n改成不限品牌", None),
        ("想喝50嵐\n不要50嵐", None),
    ],
)
def test_chat_brand_corrections_do_not_reintroduce_old_model_tags(text, brand):
    facts = IntakeFacts(
        title="飲料需求", category="drink", tags=["品牌:50嵐", *[f"偏好{i}" for i in range(11)]]
    )
    value, _, _ = prepare(facts.title, [], now=NOW, facts=facts, original=text)
    assert [tag for tag in value.tags if tag.startswith("品牌:")] == (
        ["品牌:" + brand] if brand else []
    )


def test_essential_resource_and_invalid_window_questions_precede_optional_fields():
    service = Mock()
    service.signals.return_value = SimpleNamespace(items=[])
    interpreter = Mock()
    for facts, field in (
        (IntakeFacts(title="提供披薩", category="pizza", kind="offer"), "roles"),
        (IntakeFacts(title="一週後喝飲料", category="drink", delay_minutes=10065), "delay_minutes"),
    ):
        interpreter.interpret.return_value = review(facts, "synthetic")
        result = interview(
            service, IntakeRequest(turns=[facts.title]), interpreter=interpreter, now=NOW
        )
        assert result.signal is None and result.questions[0].field == field


def test_explicit_drink_brand_corrects_a_model_category_without_overriding_later_food_changes():
    service = Mock()
    service.signals.return_value = SimpleNamespace(items=[])
    interpreter = Mock()
    interpreter.interpret.return_value = review(
        IntakeFacts(title="提供50嵐優惠券", kind="offer", category="pizza", roles=["coupon"]),
        "synthetic",
    )
    result = interview(
        service, IntakeRequest(turns=["我有50嵐買一送一優惠券"]), interpreter=interpreter, now=NOW
    )
    assert result.facts.category == "drink" and result.signal.category == "drink"
    assert "品牌:50嵐" in result.signal.tags
    facts = IntakeFacts(title="改吃披薩", category="pizza")
    signal = prepare(facts.title, [], facts=facts, original="我想喝50嵐\n改成披薩", now=NOW)[0]
    assert signal.category == "pizza" and not any(tag.startswith("品牌:") for tag in signal.tags)
