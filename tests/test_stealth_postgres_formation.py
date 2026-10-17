"""Record-graph tests with a recording session; not PostgreSQL integration tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import SessionTransactionOrigin

from app.models.domain import (
    Conversation,
    ConversationMember,
    JoinRequest,
    Notification,
    Post,
    Profile,
)
from app.models.enums import JoinRequestStatus, PostStatus
from app.models.stealth import (
    StealthAgent,
    StealthEvent,
    StealthFormation,
    StealthProposal,
    StealthProposalMember,
    StealthSignal,
)
from app.stealth.errors import StealthError
from app.stealth.postgres import LockedProposal
from app.stealth.postgres_formation import PostgresFormationService
from app.stealth.rules import payload_digest
from app.stealth.service import StealthService
from app.stealth.storage import LocalStore
from tests.test_stealth import NOW, accept_all, formation_input, setup_group


class RecordingSession:
    def __init__(self):
        self.records = []
        self.scalar = AsyncMock(return_value=None)
        self.flush = AsyncMock()
        self.commit = Mock(side_effect=AssertionError("caller owns commit"))

    def get_transaction(self):
        return SimpleNamespace(
            sync_transaction=SimpleNamespace(origin=SessionTransactionOrigin.BEGIN)
        )

    def add(self, record):
        self.records.append(record)

    def add_all(self, records):
        self.records.extend(records)


@pytest.fixture
def graph(tmp_path):
    local = StealthService(LocalStore(tmp_path / "source-graph.sqlite3"))
    users, signals = setup_group(local)
    invite = local.run_matches(users[0], 1, NOW)[0]
    accept_all(local, users, invite.id)
    with local.store.transaction() as unit:
        domain = unit.get("proposals", invite.id)
    members = [
        StealthProposalMember(
            proposal_id=domain.id,
            user_id=m.user_id,
            signal_id=m.signal_id,
            signal_version=m.signal_version,
            agent_version=m.agent_version,
            decision=m.decision,
            decided_at=m.decided_at,
            decline_reason=m.decline_reason,
        )
        for m in domain.members
    ]
    rows = [
        StealthSignal(
            id=s.id,
            owner_id=s.owner_id,
            kind=s.kind.value,
            category=s.category,
            title=s.title,
            location_id=s.location_id,
            starts_at=s.starts_at,
            ends_at=s.ends_at,
            expires_at=s.expires_at,
            budget_max_twd=s.budget_max_twd,
            known_cost_twd=s.known_cost_twd,
            version=s.version,
            status=s.status.value,
            created_at=s.created_at,
            updated_at=NOW,
            attributes={
                "roles": s.roles,
                "tags": s.tags,
                "acceptable_categories": s.acceptable_categories,
                "dietary_requirements": s.dietary_requirements,
                "dietary_verified": s.dietary_verified,
            },
        )
        for s in signals
    ]
    proposal = StealthProposal(
        id=domain.id,
        fingerprint=domain.fingerprint,
        category=domain.category,
        location_id=domain.location_id,
        starts_at=domain.starts_at,
        ends_at=domain.ends_at,
        expires_at=domain.expires_at,
        budget_max_twd=domain.budget_max_twd,
        known_cost_twd=domain.known_cost_twd,
        score=domain.score,
        status=domain.status.value,
        explanation={"reasons": domain.reasons, "unknowns": domain.unknowns},
        algorithm_version="constraint_rules_v1",
        created_at=NOW,
        updated_at=NOW,
    )
    agents = [
        StealthAgent(
            user_id=user,
            enabled=True,
            name="Synthetic",
            daily_invite_limit=3,
            timezone="Asia/Taipei",
            version=1,
            policy_version=1,
            settings={},
            created_at=NOW,
            updated_at=NOW,
        )
        for user in users
    ]
    return users, LockedProposal(proposal, members, rows, agents)


def recording_service(locked):
    session = RecordingSession()
    service = PostgresFormationService(session)
    service.repository = SimpleNamespace(
        lock_for_actor=AsyncMock(return_value=locked.proposal),
        lock_ready_proposal=AsyncMock(return_value=locked),
    )
    return service, session


@pytest.mark.asyncio
async def test_formation_builds_existing_post_join_request_and_chat_models(graph):
    users, locked = graph
    service, session = recording_service(locked)
    result = await service.form(users[0], locked.proposal.id, formation_input(), NOW)
    assert result.storage == "postgresql"
    posts = [r for r in session.records if isinstance(r, Post)]
    assert len(posts) == 1 and posts[0].capacity == 3 and posts[0].status == PostStatus.FULL
    assert posts[0].content == formation_input().content
    chats = [r for r in session.records if isinstance(r, Conversation)]
    assert len(chats) == 1 and chats[0].post_id == posts[0].id
    chat_members = [r for r in session.records if isinstance(r, ConversationMember)]
    assert {r.user_id for r in chat_members} == set(users)
    assert {r.conversation_id for r in chat_members} == {result.conversation_id}
    requests = [r for r in session.records if isinstance(r, JoinRequest)]
    assert {r.requester_id for r in requests} == set(users[1:])
    assert all(r.status == JoinRequestStatus.ACCEPTED and r.decided_at == NOW for r in requests)
    formations = [r for r in session.records if isinstance(r, StealthFormation)]
    assert len(formations) == 1 and formations[0].post_id == result.post_id
    assert all(s.status == "consumed" and s.version == 2 for s in locked.signals)
    assert locked.proposal.status == "formed"
    assert len([r for r in session.records if isinstance(r, StealthEvent)]) == 1
    notifications = [r for r in session.records if isinstance(r, Notification)]
    assert {r.user_id for r in notifications} == set(users)
    assert all("private raw text" not in r.body for r in notifications)
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_terms_do_not_begin_writing_records(graph):
    users, locked = graph
    service, session = recording_service(locked)
    with pytest.raises(StealthError, match="cost_exceeds_budget"):
        await service.form(
            users[0], locked.proposal.id, formation_input(confirmed_cost_twd=151), NOW
        )
    assert session.records == []
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_returns_existing_records_without_consuming_more_resources(graph):
    users, locked = graph
    service, session = recording_service(locked)
    locked.proposal.status = "formed"
    existing = StealthFormation(
        id=uuid4(),
        proposal_id=locked.proposal.id,
        host_id=users[0],
        post_id=uuid4(),
        conversation_id=uuid4(),
        created_at=NOW,
        confirmed_terms={"payload_hash": payload_digest(formation_input())},
    )
    session.scalar.return_value = existing
    result = await service.form(users[0], locked.proposal.id, formation_input(), NOW)
    assert result.id == existing.id and session.records == []
    service.repository.lock_ready_proposal.assert_not_awaited()
    with pytest.raises(StealthError, match="idempotency_payload_conflict"):
        await service.form(
            users[0], locked.proposal.id, formation_input(confirmed_cost_twd=140), NOW
        )
    with pytest.raises(StealthError, match="already_formed_by_other_host"):
        await service.form(users[1], locked.proposal.id, formation_input(), NOW)


@pytest.mark.asyncio
async def test_explicit_transaction_is_required_before_any_database_call():
    async with AsyncSession() as session:
        service = PostgresFormationService(session)
        with pytest.raises(RuntimeError, match="explicit_transaction"):
            await service.form(uuid4(), uuid4(), formation_input(), NOW)
        # Adding an object starts AUTOBEGIN without making a database connection.
        session.add(Profile(id=uuid4(), display_name="Synthetic"))
        with pytest.raises(RuntimeError, match="explicit_transaction"):
            await service.form(uuid4(), uuid4(), formation_input(), NOW)
