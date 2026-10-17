"""Bounded, deterministic matching. Scores are heuristics, never probabilities."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from itertools import combinations
from uuid import UUID, uuid4

from app.stealth.schemas import (
    Agent,
    Proposal,
    ProposalMember,
    Signal,
    SignalKind,
)

MAX_CANDIDATES = 60


def categories(signal: Signal) -> set[str]:
    """Alternatives must have been explicitly confirmed, never inferred at matching time."""
    return {signal.category, *signal.acceptable_categories}


def fingerprint(signals: list[Signal], agents: dict[UUID, Agent]) -> str:
    parts = sorted(f"{s.id}:{s.version}:{agents[s.owner_id].policy_version}" for s in signals)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def build_candidate(
    signals: list[Signal],
    agents: dict[UUID, Agent],
    blocked: set[frozenset[UUID]],
    now: datetime,
) -> Proposal | None:
    if not 2 <= len(signals) <= 3 or len({s.owner_id for s in signals}) != len(signals):
        return None
    if not any(s.kind == SignalKind.NEED for s in signals):
        return None
    common_categories = set.intersection(*(categories(s) for s in signals))
    if len({s.location_id for s in signals}) != 1 or not common_categories:
        return None
    resource_categories = {s.category for s in signals if s.kind == SignalKind.OFFER}
    chosen_category = min(resource_categories & common_categories or common_categories)
    for signal in signals:
        agent = agents.get(signal.owner_id)
        if not signal.usable_at(now) or not agent or not agent.enabled or agent.quiet_at(now):
            return None
    for a, b in combinations(signals, 2):
        if frozenset((a.owner_id, b.owner_id)) in blocked:
            return None
    start = max(now, *(s.starts_at for s in signals))
    end = min(s.ends_at for s in signals)
    expiry = min(now + timedelta(minutes=30), start, *(s.expires_at for s in signals))
    # If pickup has already started, give invitations only the remaining valid window.
    if start == now:
        expiry = min(now + timedelta(minutes=30), end, *(s.expires_at for s in signals))
    if start >= end or expiry <= now:
        return None
    budgets = [s.budget_max_twd for s in signals if s.budget_max_twd is not None]
    budget = min(budgets) if budgets else None
    costs = {s.known_cost_twd for s in signals if s.known_cost_twd is not None}
    if len(costs) > 1 or (costs and budget is not None and max(costs) > budget):
        return None
    # Guarantees are only user-supplied claims, not an allergy safety certification.
    requirements = set().union(*(set(s.dietary_requirements) for s in signals))
    food_sources = [s for s in signals if s.kind != SignalKind.PLAN]
    if requirements and any(not requirements <= set(s.dietary_verified) for s in food_sources):
        return None
    common_tags = set.intersection(*(set(s.tags) for s in signals))
    # Familiar mode requires a shared declared tag for two-person need+need pairs.
    if all(s.kind == SignalKind.NEED for s in signals) and not common_tags:
        if any(agents[s.owner_id].exploration == "familiar" for s in signals):
            return None
    kinds = {s.kind for s in signals}
    roles = set().union(*(set(s.roles) for s in signals))
    reasons = ["取餐地點一致", "可用時間有交集", "餐點分類符合本人確認的選項"]
    if len({s.category for s in signals}) > 1:
        reasons.append("透過本人明確接受的替代類型形成互補方案")
    if common_tags:
        reasons.append("有共同的餐點偏好")
    if SignalKind.OFFER in kinds:
        reasons.append("有人願意提供資源")
    if SignalKind.PLAN in kinds:
        reasons.append("有人明確表示願意順路取餐")
    if budget is not None:
        reasons.append("方案預算上限採所有已提供上限中的最低值")
    unknowns = []
    if not costs:
        unknowns.append("實際每人費用尚未確認")
    if "order" not in roles:
        unknowns.append("尚需確認下單者")
    if "pickup" not in roles:
        unknowns.append("尚需確認取餐者")
    if "coupon" in roles:
        unknowns.append("優惠券有效性與使用條件需由持有人確認")
    if requirements:
        unknowns.append("飲食資訊為參與者提供，仍須向商家確認；不保證過敏安全")
    score = min(
        100,
        40
        + 10 * len(common_tags)
        + 10 * (len(kinds) - 1)
        + 5 * len(roles)
        + 5 * (len(signals) - 2),
    )
    return Proposal(
        id=uuid4(),
        fingerprint=fingerprint(signals, agents),
        category=chosen_category,
        location_id=signals[0].location_id,
        starts_at=start,
        ends_at=end,
        expires_at=expiry,
        budget_max_twd=budget,
        known_cost_twd=next(iter(costs)) if costs else None,
        score=score,
        reasons=reasons,
        unknowns=unknowns,
        members=[
            ProposalMember(
                user_id=s.owner_id,
                signal_id=s.id,
                signal_version=s.version,
                agent_version=agents[s.owner_id].policy_version,
            )
            for s in sorted(signals, key=lambda item: str(item.owner_id))
        ],
        created_at=now,
    )


def find_candidates(
    actor: UUID,
    signals: list[Signal],
    agents: dict[UUID, Agent],
    blocked: set[frozenset[UUID]],
    now: datetime,
) -> list[Proposal]:
    """Bound enumeration. Never generate a proposal unrelated to its requesting user."""
    own = sorted(
        (s for s in signals if s.owner_id == actor and s.usable_at(now)),
        key=lambda s: (s.created_at, str(s.id)),
        reverse=True,
    )[:10]
    other = sorted(
        (
            s
            for s in signals
            if s.owner_id != actor
            and s.usable_at(now)
            and s.owner_id in agents
            and agents[s.owner_id].enabled
            and not agents[s.owner_id].quiet_at(now)
            and frozenset((actor, s.owner_id)) not in blocked
        ),
        key=lambda s: (s.created_at, str(s.id)),
        reverse=True,
    )
    result: dict[str, Proposal] = {}
    for anchor in own:
        compatible = [
            s
            for s in other
            if categories(s) & categories(anchor)
            and s.location_id == anchor.location_id
            and max(s.starts_at, anchor.starts_at, now) < min(s.ends_at, anchor.ends_at)
        ][:MAX_CANDIDATES]
        for count in (1, 2):
            for group in combinations(compatible, count):
                candidate = build_candidate([anchor, *group], agents, blocked, now)
                if candidate:
                    result[candidate.fingerprint] = candidate
    return sorted(result.values(), key=lambda p: (-p.score, p.fingerprint))
