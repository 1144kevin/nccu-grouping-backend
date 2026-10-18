"""Local matching integration: hard rules authorize every candidate before model work."""

from datetime import datetime
from uuid import UUID

from app.stealth.matching import find_candidates
from app.stealth.provider_contracts import CandidateContext
from app.stealth.schemas import Agent, ProposalView, Signal, StrictModel
from app.stealth.semantic import SemanticMatcher, SemanticRanking


class SemanticMatchRun(StrictModel):
    ranking: SemanticRanking
    created: int
    items: list[ProposalView]


def semantic_candidates(
    actor: UUID,
    signals: list[Signal],
    agents: dict[UUID, Agent],
    blocked: set[frozenset[UUID]],
    now: datetime,
    matcher: SemanticMatcher,
    *,
    review_limit=3,
) -> SemanticRanking:
    proposals = find_candidates(actor, signals, agents, blocked, now)[:12]
    by_id = {signal.id: signal for signal in signals}
    contexts = []
    for proposal in proposals:
        others = [by_id[m.signal_id] for m in proposal.members if m.user_id != actor]
        facts = ["location", "time", "category"]
        if any(s.kind == "offer" for s in others):
            facts.append("offer")
        if any("pickup" in s.roles for s in others):
            facts.append("pickup_volunteer")
        if proposal.known_cost_twd is None:
            facts.append("cost_unknown")
        roles = {r for m in proposal.members for r in by_id[m.signal_id].roles}
        if "order" not in roles:
            facts.append("order_missing")
        if "pickup" not in roles:
            facts.append("pickup_missing")
        if "coupon" in roles:
            facts.append("coupon_terms")
        # Do not embed the anchor's own text into candidates: that would inflate similarity.
        document = "\n".join(
            s.title + "；偏好：" + "、".join(s.tags) + "；志願：" + "、".join(s.roles)
            for s in others
        )
        contexts.append(CandidateContext(key=proposal.fingerprint, document=document, facts=facts))
    own = [s.title for s in signals if s.owner_id == actor and s.usable_at(now)]
    return matcher.rank("\n".join(own[:10]), contexts, review_limit=review_limit)


def run_local_semantic(service, actor: UUID, maximum: int, matcher: SemanticMatcher):
    from app.stealth.service import utc_now

    with service.store.transaction() as unit:
        signals = unit.all("signals")
        agents = {agent.user_id: agent for agent in unit.all("agents")}
        blocked = unit.blocked_pairs()
    ranking = semantic_candidates(actor, signals, agents, blocked, utc_now(), matcher)
    # Never hold a SQLite transaction during inference. Recompute eligibility, versions,
    # blocks, quotas and cooldown in the existing atomic write path with a fresh clock.
    items = service.run_matches(
        actor, maximum, utc_now(), preferred_order=[item.key for item in ranking.items]
    )
    with service.store.transaction() as unit:
        fresh_signals = unit.all("signals")
        fresh_agents = {agent.user_id: agent for agent in unit.all("agents")}
        allowed = {
            p.fingerprint
            for p in find_candidates(
                actor, fresh_signals, fresh_agents, unit.blocked_pairs(), utc_now()
            )
        }
    if any(item.key not in allowed for item in ranking.items):
        ranking.warnings.append("配對條件已變更或失效，已移除舊排序；請重新整理")
    ranking.items = [item for item in ranking.items if item.key in allowed]
    return SemanticMatchRun(ranking=ranking, created=len(items), items=items)
