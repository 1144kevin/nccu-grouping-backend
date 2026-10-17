"""Shared consent/formation rules for local and PostgreSQL adapters."""

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.stealth.errors import StealthError
from app.stealth.matching import categories
from app.stealth.schemas import Agent, FormationInput, Proposal, Signal, SignalKind


def payload_digest(value: BaseModel) -> str:
    canonical = json.dumps(
        value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def snapshot_is_valid(
    proposal: Proposal,
    signals: Mapping[UUID, Signal | None],
    agents: Mapping[UUID, Agent | None],
    blocked: set[frozenset[UUID]],
    now: datetime,
) -> bool:
    if proposal.expires_at <= now or proposal.ends_at <= now:
        return False
    if proposal.status == "ready" and any(
        m.decision != "accepted" or m.accepted_terms_revision != proposal.terms_revision
        for m in proposal.members
    ):
        return False
    sources = []
    for member in proposal.members:
        signal, agent = signals.get(member.signal_id), agents.get(member.user_id)
        if not signal or not agent or not agent.enabled or not signal.usable_at(now):
            return False
        if signal.owner_id != member.user_id:
            return False
        if signal.version != member.signal_version or agent.policy_version != member.agent_version:
            return False
        if (
            proposal.category not in categories(signal)
            or signal.location_id != proposal.location_id
        ):
            return False
        if not signal.starts_at <= proposal.starts_at < proposal.ends_at <= signal.ends_at:
            return False
        if proposal.expires_at > signal.expires_at:
            return False
        sources.append(signal)
    if not any(signal.kind == SignalKind.NEED for signal in sources):
        return False
    users = [member.user_id for member in proposal.members]
    if any(
        frozenset((left, right)) in blocked for left in users for right in users if left != right
    ):
        return False
    budgets = [s.budget_max_twd for s in sources if s.budget_max_twd is not None]
    if proposal.budget_max_twd != (min(budgets) if budgets else None):
        return False
    costs = {s.known_cost_twd for s in sources if s.known_cost_twd is not None}
    if len(costs) > 1 or proposal.known_cost_twd != (next(iter(costs)) if costs else None):
        return False
    if costs and budgets and next(iter(costs)) > min(budgets):
        return False
    requirements = set().union(*(set(s.dietary_requirements) for s in sources))
    return not requirements or all(
        requirements <= set(s.dietary_verified) for s in sources if s.kind != SignalKind.PLAN
    )


def validate_plan_constraints(
    proposal: Proposal,
    signals: list[Signal],
    value: FormationInput,
    now: datetime,
) -> None:
    if not now < value.deadline_at <= min(proposal.expires_at, proposal.ends_at):
        raise StealthError(422, "invalid_deadline")
    if proposal.budget_max_twd is not None and value.confirmed_cost_twd > proposal.budget_max_twd:
        raise StealthError(422, "cost_exceeds_budget")
    if proposal.known_cost_twd is not None and value.confirmed_cost_twd != proposal.known_cost_twd:
        raise StealthError(409, "known_cost_changed_requires_new_proposal")
    if not value.confirmed_roles.get("order") or not value.confirmed_roles.get("pickup"):
        raise StealthError(422, "confirm_order_and_pickup_roles")
    authorized = set(value.host_roles).union(*(set(signal.roles) for signal in signals))
    if not {"order", "pickup"} <= authorized:
        raise StealthError(422, "roles_not_authorized_by_participants")


def validate_formation_terms(
    proposal: Proposal,
    signals: list[Signal],
    value: FormationInput,
    now: datetime,
    actor: UUID,
) -> None:
    if proposal.status != "ready" or any(
        m.decision != "accepted" or m.accepted_terms_revision != proposal.terms_revision
        for m in proposal.members
    ):
        raise StealthError(409, "proposal_not_ready")
    validate_plan_constraints(proposal, signals, value, now)
    if proposal.terms is not None:
        if proposal.terms_author_id != actor:
            raise StealthError(409, "final_terms_proposer_must_host")
        if payload_digest(value) != payload_digest(proposal.terms):
            raise StealthError(409, "final_terms_changed_requires_reconfirmation")
    elif proposal.known_cost_twd is None and any(s.budget_max_twd is None for s in signals):
        raise StealthError(409, "missing_budget_requires_final_terms")
