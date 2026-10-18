import pytest

from app.core_cloud.grounding import integer, minutes_supported, money_supported
from app.core_cloud.intake import IntakeFacts, ground, review


@pytest.mark.parametrize(
    "text,value",
    [("一百二十", 120), ("兩千零三", 2003), ("十", 10), ("0", 0), ("120.0", 120), ("1.2", None)],
)
def test_money_numbers_preserve_chinese_units_and_reject_rounding(text, value):
    assert integer(text) == value


@pytest.mark.parametrize(
    "minutes,quote,start,valid",
    [
        (30, "半小時後", True, True),
        (90, "一個半小時", False, True),
        (90, "1.5小時後", True, True),
        (60, "今晚六點", True, False),
        (60, "持續一小時", True, False),
        (0, "現在開始", True, True),
        (120, "持續兩小時", False, True),
        (20, "持續兩小時", False, False),
    ],
)
def test_relative_time_requires_units_and_start_evidence(minutes, quote, start, valid):
    assert minutes_supported(minutes, quote, start=start) is valid


def test_fractional_amount_cannot_become_integer_or_zero():
    assert not money_supported(120, "每人120.5元")
    assert not money_supported(0, "每人0.5元")
    assert money_supported(0, "免費分食")
    assert not money_supported(0, "不是免費")
    assert not money_supported(0, "一二〇元")
    assert not money_supported(120, "共120人")


def test_budget_and_individual_cost_must_use_their_own_amounts():
    assert money_supported(120, "每人預算只有120元，實際報價90元", basis="budget")
    assert not money_supported(90, "每人預算只有120元，實際報價90元", basis="budget")
    assert money_supported(90, "整團總價540元，已確認每人90元", basis="cost")
    assert not money_supported(540, "整團總價540元，已確認每人90元", basis="cost")
    assert not money_supported(2, "一份100元，共2份", basis="cost")


def test_invented_quote_clears_numbers_and_returns_questions():
    facts = IntakeFacts(
        title="需求",
        budget_max_twd=120,
        known_cost_twd=90,
        delay_minutes=60,
        duration_minutes=120,
        evidence={"budget": "預算120", "cost": "每人90", "start": "一小時後", "duration": "兩小時"},
    )
    assert ground(facts, "想吃披薩")
    assert all(
        getattr(facts, key) is None
        for key in ("budget_max_twd", "known_cost_twd", "delay_minutes", "duration_minutes")
    )
    assert not review(facts, "test").ready_for_review


def test_price_cannot_use_budget_quote_and_group_total_requires_clarification():
    for quote in ("每人預算120元", "整團總價120元"):
        facts = IntakeFacts(title="需求", known_cost_twd=120, evidence={"cost": quote})
        ground(facts, quote)
        assert facts.known_cost_twd is None
    assert facts.cost_needs_clarification


def test_narrow_role_quote_cannot_hide_negation_in_original():
    facts = IntakeFacts(
        title="需求", roles=["pickup"], roles_confirmed=True, evidence={"roles": "願意取餐"}
    )
    ground(facts, "我不願意取餐")
    assert facts.roles == [] and not facts.roles_confirmed


def test_one_declined_role_does_not_answer_every_role_question():
    facts = IntakeFacts(
        title="需求", roles=[], roles_confirmed=True, evidence={"roles": "不方便取餐"}
    )
    ground(facts, "不方便取餐")
    assert not facts.roles_confirmed
    assert "roles" in [q.field for q in review(facts, "test").questions]


def test_narrow_free_quote_cannot_hide_negated_price():
    facts = IntakeFacts(title="需求", known_cost_twd=0, evidence={"cost": "免費"})
    ground(facts, "不是免費，費用未知")
    assert facts.known_cost_twd is None


def test_explicit_unknown_price_does_not_cause_endless_cost_question():
    facts = IntakeFacts(
        title="需求",
        known_cost_twd=100,
        cost_needs_clarification=True,
        evidence={"cost": "費用未知"},
    )
    ground(facts, "費用未知")
    assert facts.known_cost_twd is None and not facts.cost_needs_clarification


@pytest.mark.parametrize(
    "original,quote",
    [
        ("希望有人願意取餐", "願意取餐"),
        ("我朋友說他願意取餐", "願意取餐"),
        ("你覺得我應該願意取餐嗎？", "願意取餐"),
        ("如果我願意取餐呢？", "我願意取餐"),
        ("我可能可以取餐", "可以取餐"),
    ],
)
def test_third_party_or_question_context_cannot_become_personal_volunteering(original, quote):
    facts = IntakeFacts(
        title="需求", roles=["pickup"], roles_confirmed=True, evidence={"roles": quote}
    )
    ground(facts, original)
    assert not facts.roles and not facts.roles_confirmed
    assert "roles" in [q.field for q in review(facts, "test").questions]


def test_explicit_personal_volunteering_is_retained():
    facts = IntakeFacts(
        title="需求", roles=["pickup"], roles_confirmed=True, evidence={"roles": "我願意取餐"}
    )
    ground(facts, "我願意取餐，其他事情還不知道。")
    assert facts.roles == ["pickup"] and facts.roles_confirmed


@pytest.mark.parametrize("currency", ["美元", "日圓", "人民幣", "歐元", "USD"])
def test_foreign_currency_is_not_silently_used_as_twd(currency):
    facts = IntakeFacts(
        title="需求",
        known_cost_twd=5,
        budget_max_twd=200,
        evidence={"cost": "每人5", "budget": "台幣預算200元"},
    )
    ground(facts, f"每人5{currency}，台幣預算200元。")
    assert facts.known_cost_twd is None and facts.cost_needs_clarification
    assert facts.budget_max_twd == 200
