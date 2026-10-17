"""Caller-owned PostgreSQL formation transaction; not wired to the local API.

Use inside `async with session.begin()`. No engine, external calls, commit, retries,
or Supabase credentials are created here. Actual PostgreSQL integration is pending.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import SessionTransactionOrigin

from app.models.domain import (
    Conversation,
    ConversationMember,
    JoinRequest,
    Notification,
    Post,
)
from app.models.enums import JoinRequestStatus, PostStatus
from app.models.stealth import StealthEvent, StealthFormation
from app.stealth.errors import StealthError
from app.stealth.postgres import PostgresStealthRepository, proposal_from_rows, signal_from_row
from app.stealth.rules import payload_digest, validate_formation_terms
from app.stealth.schemas import Formation, FormationInput


def formation_from_row(row: StealthFormation) -> Formation:
    return Formation(
        id=row.id,
        proposal_id=row.proposal_id,
        host_id=row.host_id,
        post_id=row.post_id,
        conversation_id=row.conversation_id,
        created_at=row.created_at,
        storage="postgresql",
    )


class PostgresFormationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = PostgresStealthRepository(session)

    async def form(
        self,
        actor: UUID,
        proposal_id: UUID,
        value: FormationInput,
        now: datetime,
    ) -> Formation:
        transaction = self.session.get_transaction()
        if (
            transaction is None
            or transaction.sync_transaction.origin != SessionTransactionOrigin.BEGIN
        ):
            raise RuntimeError("formation_requires_explicit_transaction")
        proposal = await self.repository.lock_for_actor(actor, proposal_id)
        existing = await self.session.scalar(
            select(StealthFormation).where(
                StealthFormation.proposal_id == proposal_id,
            )
        )
        if existing:
            if proposal.status != "formed":
                raise StealthError(409, "formation_state_conflict")
            if existing.host_id != actor:
                raise StealthError(409, "already_formed_by_other_host")
            if existing.confirmed_terms.get("payload_hash") != payload_digest(value):
                raise StealthError(409, "idempotency_payload_conflict")
            return formation_from_row(existing)
        locked = await self.repository.lock_ready_proposal(actor, proposal_id, now)
        proposal = locked.proposal
        domain = proposal_from_rows(locked.proposal, locked.members)
        validate_formation_terms(
            domain,
            [signal_from_row(s) for s in locked.signals],
            value,
            now,
            actor,
        )
        post = Post(
            id=uuid4(),
            author_id=actor,
            content=value.content,
            location_text=domain.location_id,
            deadline_at=value.deadline_at,
            capacity=len(locked.members),
            status=PostStatus.FULL,
            tags=["偷揪", domain.category],
            created_at=now,
            updated_at=now,
        )
        self.session.add(post)
        await self.session.flush()
        conversation = Conversation(id=uuid4(), post_id=post.id, created_at=now, updated_at=now)
        self.session.add(conversation)
        await self.session.flush()
        members = [m.user_id for m in locked.members]
        self.session.add_all(
            [
                ConversationMember(
                    conversation_id=conversation.id, user_id=user, joined_at=now, muted=False
                )
                for user in members
            ]
        )
        self.session.add_all(
            [
                JoinRequest(
                    id=uuid4(),
                    post_id=post.id,
                    requester_id=user,
                    message="",
                    status=JoinRequestStatus.ACCEPTED,
                    decided_at=now,
                    created_at=now,
                    updated_at=now,
                )
                for user in members
                if user != actor
            ]
        )
        formation = StealthFormation(
            id=uuid4(),
            proposal_id=proposal.id,
            host_id=actor,
            post_id=post.id,
            conversation_id=conversation.id,
            created_at=now,
            updated_at=now,
            confirmed_terms={
                **value.model_dump(mode="json", exclude={"content"}),
                "payload_hash": payload_digest(value),
                "location_id": domain.location_id,
                "pickup_starts_at": domain.starts_at.isoformat(),
                "pickup_ends_at": domain.ends_at.isoformat(),
            },
        )
        self.session.add(formation)
        for signal in locked.signals:
            signal.status, signal.version, signal.updated_at = "consumed", signal.version + 1, now
        proposal.status, proposal.updated_at = "formed", now
        self.session.add_all(
            [
                Notification(
                    id=uuid4(),
                    user_id=user,
                    kind="stealth_formed",
                    title="偷揪成團",
                    body="合作方案已成立，請到聊天室確認細節。",
                    target_path=f"/chat/{conversation.id}",
                    payload={},
                    created_at=now,
                )
                for user in members
            ]
        )
        self.session.add(
            StealthEvent(
                id=uuid4(),
                actor_id=actor,
                proposal_id=proposal.id,
                kind="formed",
                at=now,
                metadata_json={"formation_id": str(formation.id)},
            )
        )
        await self.session.flush()
        return formation_from_row(formation)
