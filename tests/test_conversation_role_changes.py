from unittest.mock import Mock

import pytest

from app.core_cloud.conversation import ConversationInput, NaturalTurn, converse
from app.core_cloud.intake import IntakeFacts


@pytest.mark.parametrize(
    "correction", ["我其實不願意取餐", "我不想拿餐了", "我其實不願意", "我不想出門", "我想待在宿舍"]
)
def test_latest_refusal_removes_old_pickup_and_plan_even_when_model_repeats_old_memory(correction):
    previous = IntakeFacts(
        title="喝飲料",
        kind="plan",
        category="drink",
        roles=["pickup"],
        roles_confirmed=True,
        budget_max_twd=80,
        location_id="main_gate",
        evidence={"roles": "我願意取餐", "budget": "預算80元"},
    )
    model = Mock()
    model.respond.return_value = NaturalTurn(
        facts=previous.model_copy(deep=True),
        message="你願意取餐，還有其他願意協助的分工嗎？",
        question_field="roles",
    )
    result = converse(
        Mock(),
        ConversationInput(
            turns=["想喝飲料，政大正門，預算80元，我願意取餐", correction],
            previous=previous,
            assistant_turns=["那你可以取餐，我們來確認其他條件。"],
        ),
        dialogue=model,
    )
    assert result.facts.roles == [] and result.facts.roles_confirmed
    assert result.facts.kind == "need"
    assert result.facts.budget_max_twd == 80 and result.facts.location_id == "main_gate"
    assert result.signal is not None and result.signal.roles == [] and result.signal.kind == "need"
    assert "不需要你去取餐" in result.message


def test_later_correction_can_reenable_pickup_and_preserves_other_explicit_roles():
    model = Mock()
    model.respond.return_value = NaturalTurn(
        facts=IntakeFacts(title="飲料", category="drink"), message="了解，照你的新意願安排。"
    )
    result = converse(
        Mock(),
        ConversationInput(
            turns=["想喝飲料，我願意下單，我願意取餐", "我不願意取餐", "改成我可以取餐"],
            assistant_turns=["好，先整理需求。", "了解，不需取餐。"],
        ),
        dialogue=model,
    )
    assert set(result.facts.roles) == {"order", "pickup"}


@pytest.mark.parametrize("turn", ["朋友願意取餐", "如果我願意取餐呢？", "你願意取餐嗎？"])
def test_third_party_or_hypothetical_text_does_not_create_personal_commitment(turn):
    model = Mock()
    model.respond.return_value = NaturalTurn(
        facts=IntakeFacts(title="飲料", category="drink"),
        message="想在哪裡取餐？",
        question_field="location_id",
    )
    result = converse(Mock(), ConversationInput(turns=["想喝飲料", turn]), dialogue=model)
    assert result.facts.roles == []
