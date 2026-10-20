import pytest

from app.core_cloud.intake import IntakeFacts, ground, review


@pytest.mark.parametrize(
    "field,evidence,value,text",
    [
        ("budget_max_twd", "budget", 120, "預算120元。預算還沒決定"),
        ("known_cost_twd", "cost", 90, "每人90元。報價還沒確定"),
        ("delay_minutes", "start", 60, "一小時後開始。開始時間改成今晚"),
        ("duration_minutes", "duration", 120, "持續兩小時。只能配合30分鐘"),
    ],
)
def test_obsolete_verbatim_evidence_cannot_override_corrections(field, evidence, value, text):
    quote = text.split("。")[0]
    facts = IntakeFacts(title="隔離案例", **{field: value}, evidence={evidence: quote})
    ground(facts, text)
    assert getattr(facts, field) is None
    assert not review(facts, "synthetic").ready_for_review


def test_obsolete_role_quote_is_not_a_current_commitment():
    facts = IntakeFacts(
        title="隔離案例", roles=["pickup"], roles_confirmed=True, evidence={"roles": "我願意取餐"}
    )
    ground(facts, "我願意取餐。現在取消取餐，角色還沒決定")
    assert not facts.roles and not facts.roles_confirmed


def test_explicit_numeric_budget_correction_uses_latest_literal():
    facts = IntakeFacts(title="需求", budget_max_twd=150, evidence={"budget": "預算150元"})
    ground(facts, "預算150元。預算上限改成70元，其他不變")
    assert facts.budget_max_twd == 70 and facts.evidence.budget == "預算上限改成70元"


@pytest.mark.parametrize(
    "text",
    ["如果預算改成70元呢？", "不要預算改成70元", "預算改成70元，預算仍未知", "預算改成70元美元"],
)
def test_budget_repair_never_infers_hypothetical_negated_or_superseded_value(text):
    facts = IntakeFacts(title="需求")
    ground(facts, text)
    assert facts.budget_max_twd is None
