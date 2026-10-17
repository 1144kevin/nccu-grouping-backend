"""PostgreSQL integration building blocks, deliberately not wired to live routes.

All methods use a caller-owned AsyncSession/transaction. No engine is created here.
Compilation tests verify SQL structure; real PostgreSQL integration is still required.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Block, Profile
from app.models.stealth import StealthAgent, StealthProposal, StealthProposalMember, StealthSignal
from app.stealth.errors import StealthError
from app.stealth.matching import categories
from app.stealth.rules import snapshot_is_valid
from app.stealth.schemas import Agent, Proposal, ProposalMember, Signal


def candidate_query(actor: UUID, anchor: Signal, now: datetime, limit: int = 60):
    """Authorized candidates for ONE bounded anchor, filtered before LIMIT."""
    if not 1 <= limit <= 60:
        raise ValueError("candidate limit must be between 1 and 60")
    if anchor.owner_id != actor:
        raise ValueError("anchor must belong to requesting user")
    blocked = exists(
        select(Block.id).where(
            or_(
                and_(Block.blocker_id == actor, Block.blocked_id == StealthSignal.owner_id),
                and_(Block.blocked_id == actor, Block.blocker_id == StealthSignal.owner_id),
            )
        )
    )
    allowed = sorted(categories(anchor))
    category_compatible = or_(
        StealthSignal.category.in_(allowed),
        *(
            StealthSignal.attributes["acceptable_categories"].contains([category])
            for category in allowed
        ),
    )
    return (
        select(StealthSignal)
        .join(StealthAgent, StealthAgent.user_id == StealthSignal.owner_id)
        .join(Profile, Profile.id == StealthSignal.owner_id)
        .where(
            StealthSignal.owner_id != actor,
            StealthAgent.enabled.is_(True),
            Profile.deleted_at.is_(None),
            StealthSignal.status == "active",
            StealthSignal.expires_at > now,
            StealthSignal.ends_at > now,
            category_compatible,
            StealthSignal.location_id == anchor.location_id,
            StealthSignal.starts_at < anchor.ends_at,
            StealthSignal.ends_at > anchor.starts_at,
            ~blocked,
        )
        .order_by(StealthSignal.created_at.desc(), StealthSignal.id.desc())
        .limit(limit)
    )


def signal_from_row(row: StealthSignal) -> Signal:
    attributes = row.attributes
    return Signal(
        id=row.id,
        owner_id=row.owner_id,
        kind=row.kind,
        category=row.category,
        acceptable_categories=attributes.get("acceptable_categories", []),
        title=row.title,
        location_id=row.location_id,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        expires_at=row.expires_at,
        budget_max_twd=row.budget_max_twd,
        known_cost_twd=row.known_cost_twd,
        tags=attributes.get("tags", []),
        roles=attributes.get("roles", []),
        dietary_requirements=attributes.get("dietary_requirements", []),
        dietary_verified=attributes.get("dietary_verified", []),
        status=row.status,
        version=row.version,
        created_at=row.created_at,
    )


def agent_from_row(row: StealthAgent) -> Agent:
    return Agent(
        name=row.name,
        enabled=row.enabled,
        daily_invite_limit=row.daily_invite_limit,
        timezone=row.timezone,
        quiet_start_hour=row.settings.get("quiet_start_hour", 22),
        quiet_end_hour=row.settings.get("quiet_end_hour", 8),
        exploration=row.settings.get("exploration", "familiar"),
        tone=row.settings.get("tone", "warm"),
        avatar_key=row.settings.get("avatar_key", "rice"),
        user_id=row.user_id,
        version=row.version,
        policy_version=row.policy_version,
        updated_at=row.updated_at,
    )


def proposal_from_rows(row: StealthProposal, members: list[StealthProposalMember]) -> Proposal:
    return Proposal(
        id=row.id,
        fingerprint=row.fingerprint,
        category=row.category,
        location_id=row.location_id,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        expires_at=row.expires_at,
        budget_max_twd=row.budget_max_twd,
        known_cost_twd=row.known_cost_twd,
        score=row.score,
        reasons=row.explanation.get("reasons", []),
        unknowns=row.explanation.get("unknowns", []),
        members=[
            ProposalMember(
                user_id=m.user_id,
                signal_id=m.signal_id,
                signal_version=m.signal_version,
                agent_version=m.agent_version,
                decision=m.decision,
                decided_at=m.decided_at,
                decline_reason=m.decline_reason,
                accepted_terms_revision=0 if m.decision == "accepted" else None,
            )
            for m in members
        ],
        status=row.status,
        created_at=row.created_at,
    )


@dataclass(frozen=True)
class CandidatePool:
    signals: list[Signal]
    agents: dict[UUID, Agent]
    blocks: set[frozenset[UUID]]


@dataclass(frozen=True)
class LockedProposal:
    proposal: StealthProposal
    members: list[StealthProposalMember]
    signals: list[StealthSignal]
    agents: list[StealthAgent]


class PostgresStealthRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def lock_for_actor(self, actor: UUID, proposal_id: UUID) -> StealthProposal:
        membership = await self.session.scalar(
            select(StealthProposalMember.user_id)
            .join(
                Profile,
                Profile.id == StealthProposalMember.user_id,
            )
            .where(
                StealthProposalMember.proposal_id == proposal_id,
                StealthProposalMember.user_id == actor,
                Profile.deleted_at.is_(None),
            )
        )
        if membership is None:
            raise StealthError(404, "not_found")
        proposal = await self.session.scalar(
            select(StealthProposal)
            .where(
                StealthProposal.id == proposal_id,
            )
            .with_for_update()
        )
        if proposal is None:
            raise StealthError(404, "not_found")
        return proposal

    async def candidates(self, actor: UUID, now: datetime) -> CandidatePool:
        current_agent = await self.session.scalar(
            select(StealthAgent)
            .join(Profile, Profile.id == StealthAgent.user_id)
            .where(
                StealthAgent.user_id == actor,
                Profile.deleted_at.is_(None),
            ),
        )
        if not current_agent or not current_agent.enabled:
            raise StealthError(409, "agent_not_enabled")
        if agent_from_row(current_agent).quiet_at(now):
            raise StealthError(409, "agent_quiet_hours")
        own = list(
            (
                await self.session.scalars(
                    select(StealthSignal)
                    .where(
                        StealthSignal.owner_id == actor,
                        StealthSignal.status == "active",
                        StealthSignal.expires_at > now,
                        StealthSignal.ends_at > now,
                    )
                    .order_by(StealthSignal.created_at.desc(), StealthSignal.id.desc())
                    .limit(10),
                )
            ).all()
        )
        rows = {row.id: row for row in own}
        for row in own:
            result = await self.session.scalars(candidate_query(actor, signal_from_row(row), now))
            rows.update((candidate.id, candidate) for candidate in result)
        owners = {row.owner_id for row in rows.values()} | {actor}
        agents = list(
            (
                await self.session.scalars(
                    select(StealthAgent).where(StealthAgent.user_id.in_(owners)),
                )
            ).all()
        )
        blocks = list(
            (
                await self.session.scalars(
                    select(Block).where(Block.blocker_id.in_(owners), Block.blocked_id.in_(owners)),
                )
            ).all()
        )
        return CandidatePool(
            signals=[signal_from_row(row) for row in rows.values()],
            agents={row.user_id: agent_from_row(row) for row in agents},
            blocks={frozenset((row.blocker_id, row.blocked_id)) for row in blocks},
        )

    async def lock_ready_proposal(
        self,
        actor: UUID,
        proposal_id: UUID,
        now: datetime,
    ) -> LockedProposal:
        """Lock before final formation. Caller must still atomically create records.

        Proposal -> agents in UUID order -> signals in UUID order is the lock order
        every production state transition must follow. No claim of full integration.
        """
        proposal = await self.lock_for_actor(actor, proposal_id)
        if proposal.status != "ready" or proposal.expires_at <= now:
            raise StealthError(409, "proposal_not_ready")
        members = list(
            (
                await self.session.scalars(
                    select(StealthProposalMember)
                    .where(
                        StealthProposalMember.proposal_id == proposal_id,
                    )
                    .order_by(StealthProposalMember.user_id)
                )
            ).all()
        )
        if not 2 <= len(members) <= 3 or any(m.decision != "accepted" for m in members):
            raise StealthError(409, "proposal_not_ready")
        owners = [member.user_id for member in members]
        agents = list(
            (
                await self.session.scalars(
                    select(StealthAgent)
                    .where(
                        StealthAgent.user_id.in_(owners),
                    )
                    .order_by(StealthAgent.user_id)
                    .with_for_update()
                )
            ).all()
        )
        signals = list(
            (
                await self.session.scalars(
                    select(StealthSignal)
                    .where(
                        StealthSignal.id.in_([member.signal_id for member in members]),
                    )
                    .order_by(StealthSignal.id)
                    .with_for_update()
                )
            ).all()
        )
        block = await self.session.scalar(
            select(Block.id)
            .where(
                Block.blocker_id.in_(owners),
                Block.blocked_id.in_(owners),
            )
            .limit(1)
        )
        if block:
            raise StealthError(409, "proposal_unavailable")
        if not snapshot_is_valid(
            proposal_from_rows(proposal, members),
            {s.id: signal_from_row(s) for s in signals},
            {a.user_id: agent_from_row(a) for a in agents},
            set(),
            now,
        ):
            raise StealthError(409, "proposal_unavailable")
        active_profiles = list(
            (
                await self.session.scalars(
                    select(Profile.id).where(
                        Profile.id.in_(owners),
                        Profile.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        if set(active_profiles) != set(owners):
            raise StealthError(409, "proposal_unavailable")
        return LockedProposal(proposal, members, signals, agents)
