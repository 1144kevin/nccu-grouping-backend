from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from app.core_cloud.conversation import ConversationInput, NaturalTurn, converse
from app.core_cloud.intake import IntakeFacts, ground
from app.core_cloud.quick_stealth import prepare

NOW = datetime(2026, 10, 31, 7, 0, tzinfo=UTC)


def test_spoken_payment_budget_survives_a_later_location_turn():
    provider = Mock()
    provider.respond.return_value = NaturalTurn(
        facts=IntakeFacts(title="宿舍吃分享餐", category="meal", location_id="自強10舍"),
        message="可以先看看這樣的安排是否適合。",
    )
    value = ConversationInput(
        turns=[
            "想吃肯德基分享餐，我想待在宿舍",
            "我想要大概抓我可以我可以付$200然後今天隨時都可以吃",
            "我人在政大山上的自強10舍然後我打算就在這邊取餐跟吃",
        ]
    )
    result = converse(Mock(), value, dialogue=provider, now=NOW)
    assert result.facts.budget_max_twd == 200
    assert result.signal.budget_max_twd == 200
    assert result.signal.participation_preference == "receive"
    assert result.signal.roles == []  # Receiving food is not volunteering to collect for others.
    assert result.signal.ends_at == datetime(2026, 10, 31, 16, tzinfo=UTC)
    assert result.signal.expires_at == NOW + timedelta(hours=24)


def test_lifetime_is_24h_even_when_available_for_only_one_hour():
    signal, _, _ = prepare(
        "喝飲料",
        [],
        now=NOW,
        facts=IntakeFacts(
            title="喝飲料",
            category="drink",
            duration_minutes=60,
        ),
        original="只配合一小時",
    )
    assert signal.ends_at == NOW + timedelta(hours=1)
    assert signal.expires_at == NOW + timedelta(hours=24)


def test_unknown_and_runner_fee_do_not_become_invented_budgets():
    for text in ("我可以多付10元跑腿費", "朋友可以付200", "如果我可以付200", "我不想付200"):
        facts = IntakeFacts(title="喝飲料")
        ground(facts, text)
        assert facts.budget_max_twd is None
    facts = IntakeFacts(title="喝飲料")
    ground(facts, "我可以付$200\n預算現在不確定")
    assert facts.budget_max_twd is None
