from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core_cloud.intake import IntakeFacts, review
from app.core_cloud.quick_stealth import QuickStart, prepare, start
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
    assert value.starts_at == NOW and value.ends_at == NOW + timedelta(hours=2)
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
