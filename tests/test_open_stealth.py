from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from app.core_cloud.conversation import ConversationInput, NaturalTurn, converse
from app.core_cloud.intake import IntakeFacts, ground
from app.core_cloud.quick_stealth import prepare

NOW = datetime(2026, 10, 31, 2, 0, tzinfo=UTC)  # 10:00 Taiwan


def test_open_place_survives_grounding_and_confirmation():
    facts = IntakeFacts(title="喝飲料", category="drink", location_id="台北101大門")
    ground(facts, "我在台北101大門，想喝飲料")
    signal, _, _ = prepare(facts.title, [], now=NOW, facts=facts, original="我在台北101大門")
    assert signal.location_id == "台北101大門"
    ground(facts, "我只說想喝飲料")
    assert facts.location_id is None


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("今天整天都可以", NOW, datetime(2026, 10, 31, 16, tzinfo=UTC)),
        (
            "明天整天都可以",
            datetime(2026, 10, 31, 16, tzinfo=UTC),
            datetime(2026, 11, 1, 16, tzinfo=UTC),
        ),
    ],
)
def test_full_day_is_not_reduced_to_two_hours(text, start, end):
    facts = IntakeFacts(title="喝飲料", category="drink", duration_minutes=120)
    ground(facts, text)
    signal, _, _ = prepare(facts.title, [], now=NOW, facts=facts, original=text)
    assert signal.starts_at == start
    assert signal.ends_at == end
    assert facts.duration_minutes == 1440


def test_later_shorter_window_overrides_full_day_and_negation_is_not_full_day():
    for text in ("不是整天都可以", "今天整天都可以\n改成只能一小時"):
        facts = IntakeFacts(title="喝飲料", category="drink")
        ground(facts, text)
        assert facts.duration_minutes != 1440
    facts = IntakeFacts(title="喝飲料", category="drink", duration_minutes=60)
    signal, _, _ = prepare(facts.title, [], now=NOW, facts=facts, original="整天都可以，改成一小時")
    assert signal.ends_at - signal.starts_at == timedelta(hours=1)


def test_chat_can_reply_naturally_before_a_food_category_is_known():
    provider = Mock()
    provider.respond.return_value = NaturalTurn(
        facts=IntakeFacts(title="想找人一起"), message="你今天是想找人陪，還是想一起做點什麼？"
    )
    result = converse(
        Mock(), ConversationInput(turns=["今天有點無聊，還沒想好要揪什麼"]), dialogue=provider
    )
    assert result.message == provider.respond.return_value.message
    assert result.signal is None
    assert not result.ready_for_review
