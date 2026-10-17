from datetime import timedelta
from uuid import uuid4

import pytest

from app.stealth.matching import build_candidate, find_candidates
from app.stealth.schemas import Agent, Signal
from tests.test_stealth import NOW, signal_input


def make_signal(kind="need", **changes):
    return Signal(
        **signal_input(kind, **changes).model_dump(), id=uuid4(), owner_id=uuid4(), created_at=NOW
    )


def agents_for(signals):
    return {
        s.owner_id: Agent(
            user_id=s.owner_id,
            version=1,
            updated_at=NOW,
            enabled=True,
            quiet_start_hour=None,
            quiet_end_hour=None,
        )
        for s in signals
    }


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("need", "need", True),
        ("need", "offer", True),
        ("need", "plan", True),
        ("offer", "offer", False),
        ("offer", "plan", False),
        ("plan", "plan", False),
    ],
)
def test_only_actual_demand_anchors_cooperation(left, right, expected):
    signals = [make_signal(left), make_signal(right)]
    result = build_candidate(signals, agents_for(signals), set(), NOW)
    assert (result is not None) == expected


def test_one_minute_overlap_is_preserved_and_no_false_time_claim():
    signals = [make_signal(), make_signal("offer", starts_at=NOW + timedelta(minutes=119))]
    result = build_candidate(signals, agents_for(signals), set(), NOW)
    assert result.ends_at - result.starts_at == timedelta(minutes=1)


def test_touching_windows_without_overlap_are_rejected():
    signals = [
        make_signal(),
        make_signal("offer", starts_at=NOW + timedelta(hours=2), ends_at=NOW + timedelta(hours=3)),
    ]
    assert build_candidate(signals, agents_for(signals), set(), NOW) is None


def test_conflicting_known_costs_require_confirmation_instead_of_average():
    signals = [make_signal(known_cost_twd=100), make_signal("offer", known_cost_twd=140)]
    assert build_candidate(signals, agents_for(signals), set(), NOW) is None


def test_zero_budget_is_not_treated_as_missing():
    signals = [make_signal(budget_max_twd=0), make_signal("offer", known_cost_twd=1)]
    assert build_candidate(signals, agents_for(signals), set(), NOW) is None
    signals[1].known_cost_twd = 0
    result = build_candidate(signals, agents_for(signals), set(), NOW)
    assert result.known_cost_twd == result.budget_max_twd == 0


def test_food_restriction_claims_do_not_become_medical_guarantees():
    signals = [
        make_signal(dietary_requirements=["no_beef"], dietary_verified=["no_beef"]),
        make_signal("offer", dietary_verified=["no_beef"]),
        make_signal("plan"),
    ]
    result = build_candidate(signals, agents_for(signals), set(), NOW)
    assert result is not None
    assert any("不保證過敏安全" in text for text in result.unknowns)


def test_familiar_vs_exploration_is_an_explicit_policy():
    signals = [make_signal(tags=["pizza_a"]), make_signal(tags=["pizza_b"])]
    agents = agents_for(signals)
    assert build_candidate(signals, agents, set(), NOW) is None
    for agent in agents.values():
        agent.exploration = "balanced"
    assert build_candidate(signals, agents, set(), NOW) is not None


def test_unrelated_new_signals_do_not_hide_older_compatible_signal():
    anchor, compatible = make_signal(), make_signal("offer")
    unrelated = [make_signal("offer", category="drink") for _ in range(70)]
    for signal in unrelated:
        signal.created_at = NOW + timedelta(seconds=1)
    signals = [anchor, compatible, *unrelated]
    result = find_candidates(anchor.owner_id, signals, agents_for(signals), set(), NOW)
    assert len(result) == 1
    assert any(member.signal_id == compatible.id for member in result[0].members)


def test_disabled_agents_do_not_crowd_out_valid_candidates():
    anchor, compatible = make_signal(), make_signal("offer")
    disabled = [make_signal("offer") for _ in range(70)]
    for signal in disabled:
        signal.created_at = NOW + timedelta(seconds=1)
    signals = [anchor, compatible, *disabled]
    agents = agents_for(signals)
    for signal in disabled:
        agents[signal.owner_id].enabled = False
    result = find_candidates(anchor.owner_id, signals, agents, set(), NOW)
    assert len(result) == 1


def test_explicit_flexible_dinner_need_can_use_specific_pizza_resource():
    signals = [
        make_signal(category="meal", acceptable_categories=["pizza", "bakery"]),
        make_signal("offer", category="pizza"),
        make_signal("plan", category="meal", acceptable_categories=["pizza"]),
    ]
    result = build_candidate(signals, agents_for(signals), set(), NOW)
    assert result is not None and result.category == "pizza"
    assert any("替代類型" in reason for reason in result.reasons)


def test_category_alternatives_cannot_be_assumed_without_consent():
    signals = [make_signal(category="meal"), make_signal("offer", category="pizza")]
    assert build_candidate(signals, agents_for(signals), set(), NOW) is None


def test_every_participant_must_accept_the_selected_category():
    signals = [
        make_signal(category="meal", acceptable_categories=["pizza"]),
        make_signal("offer", category="pizza"),
        make_signal("plan", category="drink"),
    ]
    assert build_candidate(signals, agents_for(signals), set(), NOW) is None
