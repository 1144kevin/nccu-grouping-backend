from datetime import UTC, datetime, timedelta
from itertools import permutations
from uuid import uuid4

import pytest

from app.core_cloud.planner import PlanningMember, plan_terms
from app.core_cloud.stealth import CloudMatch

END = datetime(2026, 10, 18, 12, tzinfo=UTC)


def plan(*, members=None, cost=90, budget=120):
    members = members or [
        PlanningMember(number=1, roles=["share"]),
        PlanningMember(number=2, roles=["order", "pickup"]),
    ]
    return plan_terms(
        category="pizza",
        location="main_gate",
        cost=cost,
        budget=budget,
        ends_at=END,
        member_count=len(members),
        members=members,
        unknowns=["優惠券有效性與使用條件需由持有人確認"],
    )


def test_known_price_and_only_volunteer_are_not_replaced_by_budget_or_coupon_guess():
    draft = plan()
    assert draft.cost_per_person_twd == 90 and draft.total_cost_twd == 180
    assert draft.order_member == draft.pickup_member == 2
    assert draft.budget_limit_twd == 120 and draft.ready_to_submit
    assert "兼任" in draft.role_suggestions[0].reason
    assert draft.is_llm is False
    assert "優惠券有效性與使用條件需由持有人確認" in draft.needs_confirmation


@pytest.mark.parametrize("budget", [None, 0, 120])
def test_unknown_price_stays_unknown_even_with_a_budget(budget):
    draft = plan(cost=None, budget=budget)
    assert draft.cost_basis == "unknown" and draft.cost_per_person_twd is None
    assert draft.total_cost_twd is None and "cost" in draft.missing_fields
    assert not draft.ready_to_submit and "NT$" not in draft.content


def test_confirmed_free_cost_stays_zero():
    draft = plan(cost=0)
    assert draft.cost_per_person_twd == draft.total_cost_twd == 0
    assert draft.cost_basis == "known_cost" and draft.ready_to_submit


def test_missing_volunteers_are_never_filled_with_the_current_user():
    draft = plan(
        members=[
            PlanningMember(number=1, roles=["share"]),
            PlanningMember(number=2, roles=["coupon"]),
        ]
    )
    assert draft.order_member is draft.pickup_member is None
    assert draft.missing_fields == ["order", "pickup"]
    assert not draft.ready_to_submit


def test_complementary_roles_are_stable_and_work_is_shared_when_possible():
    members = [
        PlanningMember(number=1, roles=["share"]),
        PlanningMember(number=2, roles=["order", "pickup"]),
        PlanningMember(number=3, roles=["pickup"]),
    ]
    for ordering in permutations(members):
        draft = plan(members=list(ordering))
        assert draft.order_member == 2 and draft.pickup_member == 3
        assert draft.total_cost_twd == 270
    both = plan(
        members=[
            PlanningMember(number=1, roles=["order", "pickup"]),
            PlanningMember(number=2, roles=["order", "pickup"]),
        ]
    )
    assert both.order_member == 1 and both.pickup_member == 2


def test_partial_or_duplicate_member_context_fails_closed():
    for members in (
        [PlanningMember(number=1, roles=["order", "pickup"])],
        [PlanningMember(number=1, roles=["order"]), PlanningMember(number=1, roles=["pickup"])],
    ):
        draft = plan_terms(
            category="pizza",
            location="main_gate",
            cost=90,
            budget=120,
            ends_at=END,
            member_count=2,
            members=members,
            unknowns=[],
        )
        assert draft.order_member is draft.pickup_member is None


def match_value():
    return dict(
        id=uuid4(),
        created_at=END - timedelta(hours=3),
        category="pizza",
        location_id="main_gate",
        starts_at=END - timedelta(hours=1),
        ends_at=END,
        expires_at=END - timedelta(hours=2),
        budget_max_twd=120,
        known_cost_twd=90,
        member_count=2,
        score=75,
        reasons=[],
        unknowns=[],
        my_decision="pending",
        status="pending",
        my_signal_title="只有本人能看見的需求原文",
        my_roles=["share"],
        invitation_text="找到互補配對",
        why_me=[],
        planning_members=[
            {"number": 1, "roles": ["share"]},
            {"number": 2, "roles": ["order", "pickup"]},
        ],
    )


def test_automatic_draft_is_person_independent_private_text_free_and_read_only():
    value = match_value()
    first = CloudMatch.model_validate(value)
    second = CloudMatch.model_validate(
        {**value, "my_member_number": 2, "my_signal_title": "另一人的私密原文"}
    )
    assert first.agent_draft == second.agent_draft
    assert "原文" not in first.agent_draft.model_dump_json()
    assert "planning_members" not in first.model_dump(mode="json")
    assert first.terms is None and first.terms_revision == first.confirmed_count == 0
    assert not first.my_terms_accepted and first.formed_post_id is None


@pytest.mark.parametrize("status", ["formed", "unavailable"])
def test_no_new_draft_for_formed_or_invalidated_proposal(status):
    assert CloudMatch.model_validate({**match_value(), "status": status}).agent_draft is None


def test_grounded_confirmed_facts_and_ai_fallback_never_invent_price_or_roles():
    from types import SimpleNamespace
    from app.core_cloud.planner_ai import polish_match_draft
    from app.stealth.ollama import LocalModelError

    match = CloudMatch.model_validate(
        {
            **match_value(),
            "known_cost_twd": None,
            "budget_max_twd": 150,
            "planning_members": [
                {"number": 1, "roles": ["share"]},
                {"number": 2, "roles": ["share"]},
            ],
            "reasons": ["對方剛好順路去政大正門", "透過需求向量搜尋找到，仍須本人確認"],
        }
    )
    assert match.agent_draft is not None
    assert match.agent_draft.cost_per_person_twd is None
    assert match.agent_draft.budget_limit_twd == 150
    assert match.agent_draft.order_member is None and match.agent_draft.pickup_member is None
    assert any("預算上限：NT$ 150" in f for f in match.agent_draft.confirmed_facts)
    assert match.agent_draft.recommendation_reasons == ["對方剛好順路去政大正門"]

    class FakeTransport:
        def __init__(self, result=None, error=None):
            self.calls = 0
            self.result = result
            self.error = error

        def request(self, *_args, **_kwargs):
            self.calls += 1
            if self.error:
                raise self.error
            return self.result

    ok = FakeTransport(
        result={
            "output_text": '{"content":"雙方皆希望在政大正門一起分享披薩，費用與分工進聊天室後確認。","recommendation_reasons":["順路去政大正門"],"needs_confirmation":["確認實際每人費用","確認下單與取餐分工"]}'
        }
    )
    res = polish_match_draft(
        match,
        settings=SimpleNamespace(stealth_ai_provider="openai", environment="local", core_cloud_enabled=True),
        transport=ok,
    )
    assert not res.fallback and ok.calls == 1
    assert res.draft.cost_per_person_twd is None
    assert res.draft.order_member is None and res.draft.pickup_member is None

    quota = FakeTransport(error=LocalModelError("openai_budget_exceeded"))
    fb = polish_match_draft(
        match,
        settings=SimpleNamespace(stealth_ai_provider="openai", environment="local", core_cloud_enabled=True),
        transport=quota,
    )
    assert fb.fallback and quota.calls == 1
    assert "額度" in fb.notice
    assert fb.draft.cost_per_person_twd is None
