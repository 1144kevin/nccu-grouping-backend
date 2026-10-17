from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.stealth.schemas import AgentInput, Signal
from app.stealth.service import StealthError, StealthService
from app.stealth.storage import LocalStore
from tests.test_stealth import (
    NOW,
    accept_all,
    formation_input,
    setup_group,
    signal_input,
)


@pytest.fixture
def service(tmp_path):
    return StealthService(LocalStore(tmp_path / "edge.sqlite3"))


def test_signal_idempotency_handles_replay_conflict_and_expiry(service):
    user = uuid4()
    service.put_agent(user, AgentInput(), NOW)
    key = uuid4()
    value = signal_input()
    signal = service.create_signal(user, value, NOW, key)
    assert service.create_signal(user, value, NOW + timedelta(hours=3), key).id == signal.id
    with pytest.raises(StealthError, match="idempotency_payload_conflict"):
        service.create_signal(user, signal_input(title="different"), NOW, key)


def test_same_idempotency_key_is_scoped_to_user(service):
    users, _ = setup_group(service)
    key = uuid4()
    signals = [service.create_signal(user, signal_input(), NOW, key) for user in users]
    assert len({s.id for s in signals}) == 3


def test_parallel_replay_creates_one_signal(service):
    user, key = uuid4(), uuid4()
    service.put_agent(user, AgentInput(), NOW)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: service.create_signal(user, signal_input(), NOW, key), range(4))
        )
    assert len({s.id for s in results}) == 1
    assert len(service.list_items(user, "signals", 50, None, NOW)[0]) == 1


def test_agent_optimistic_concurrency_rejects_lost_update(service):
    user = uuid4()
    service.put_agent(user, AgentInput(), NOW)
    with pytest.raises(StealthError, match="agent_version_conflict"):
        service.put_agent(user, AgentInput(name="stale editor"), NOW)
    assert service.get_agent(user).name != "stale editor"


def test_known_cost_cannot_be_silently_changed(service):
    users, signals = setup_group(service, ("need", "offer"))
    with service.store.transaction() as unit:
        signal = Signal(**signals[1].model_dump(exclude={"known_cost_twd"}), known_cost_twd=130)
        unit.put("signals", signal)
    proposal = service.run_matches(users[0], 1, NOW)[0]
    assert proposal.known_cost_twd == 130
    accept_all(service, users, proposal.id)
    with pytest.raises(StealthError, match="known_cost_changed_requires_new_proposal"):
        service.form(users[0], proposal.id, formation_input(confirmed_cost_twd=140), NOW)


def test_host_cannot_invent_someone_elses_role_consent(service):
    users, _ = setup_group(service, ("need", "offer"))
    proposal = service.run_matches(users[0], 1, NOW)[0]
    accept_all(service, users, proposal.id)
    with pytest.raises(StealthError, match="roles_not_authorized_by_participants"):
        service.form(users[0], proposal.id, formation_input(host_roles=[]), NOW)
    assert service.form(users[0], proposal.id, formation_input(), NOW)


def test_repeated_formation_with_changed_payload_conflicts(service):
    users, _ = setup_group(service, ("need", "offer"))
    proposal = service.run_matches(users[0], 1, NOW)[0]
    accept_all(service, users, proposal.id)
    service.form(users[0], proposal.id, formation_input(), NOW)
    with pytest.raises(StealthError, match="idempotency_payload_conflict"):
        service.form(users[0], proposal.id, formation_input(confirmed_cost_twd=140), NOW)


def test_new_block_between_accept_and_form_rejected(service):
    users, _ = setup_group(service, ("need", "offer"))
    proposal = service.run_matches(users[0], 1, NOW)[0]
    accept_all(service, users, proposal.id)
    with service.store.transaction() as unit:
        unit.connection.execute(
            "INSERT INTO local_blocks VALUES (?,?)", (str(users[0]), str(users[1]))
        )
    with pytest.raises(StealthError, match="proposal_not_ready"):
        service.form(users[0], proposal.id, formation_input(), NOW)


def test_accept_can_be_withdrawn_before_formation(service):
    users, _ = setup_group(service, ("need", "offer"))
    proposal = service.run_matches(users[0], 1, NOW)[0]
    accept_all(service, users, proposal.id)
    service.decide(users[1], proposal.id, "declined", NOW)
    with pytest.raises(StealthError, match="proposal_not_ready"):
        service.form(users[0], proposal.id, formation_input(), NOW)


def test_transaction_rolls_back_if_formation_record_creation_fails(service, monkeypatch):
    from app.stealth.storage import LocalUnitOfWork

    users, signals = setup_group(service, ("need", "offer"))
    proposal = service.run_matches(users[0], 1, NOW)[0]
    accept_all(service, users, proposal.id)

    def fail(*_):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(LocalUnitOfWork, "save_formation_records", fail)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        service.form(users[0], proposal.id, formation_input(), NOW)
    assert service.get_proposal(users[0], proposal.id, NOW).status == "ready"
    assert all(service.get_signal(s.owner_id, s.id).status == "active" for s in signals)


@pytest.mark.parametrize("cursor", ["???", "W10=", "e30=", "null", "a" * 513])
def test_malformed_cursor_is_validation_error(service, cursor):
    with pytest.raises(StealthError, match="invalid_cursor"):
        service.list_items(uuid4(), "signals", 20, cursor, NOW)


def test_active_signal_cap_does_not_consume_expired_slots(service):
    user = uuid4()
    service.put_agent(user, AgentInput(), NOW)
    for _ in range(10):
        service.create_signal(user, signal_input(), NOW)
    with pytest.raises(StealthError, match="active_signal_limit"):
        service.create_signal(user, signal_input(), NOW)
    later = NOW + timedelta(hours=3)
    assert service.create_signal(
        user,
        signal_input(
            starts_at=later + timedelta(hours=1),
            ends_at=later + timedelta(hours=2),
            expires_at=later + timedelta(minutes=30),
        ),
        later,
    )


@pytest.mark.parametrize("value", [1, "true", "yes", "1"])
def test_agent_opt_in_requires_explicit_boolean(value):
    with pytest.raises(ValidationError):
        AgentInput(enabled=value)


@pytest.mark.parametrize("value", [1, "true", "yes", "1", False])
def test_final_acknowledgement_requires_explicit_true(value):
    with pytest.raises(ValidationError):
        formation_input(acknowledge_unverified_details=value)


def test_agent_response_does_not_expose_request_lock_version(service):
    user = uuid4()
    agent = service.put_agent(user, AgentInput(), NOW)
    assert "expected_version" not in agent.model_dump()
    assert agent.version == 1


def test_cosmetic_agent_edits_preserve_existing_invitation_and_consent(service):
    users, _ = setup_group(service, ("need", "offer"))
    proposal = service.run_matches(users[0], 1, NOW)[0]
    service.decide(users[0], proposal.id, "accepted", NOW)
    old_agent = service.get_agent(users[0])
    updated = service.put_agent(
        users[0],
        AgentInput(
            enabled=True,
            name="新名字",
            tone="playful",
            avatar_key="cat",
            expected_version=old_agent.version,
            quiet_start_hour=None,
            quiet_end_hour=None,
        ),
        NOW,
    )
    assert updated.version == old_agent.version + 1
    assert updated.policy_version == old_agent.policy_version
    refreshed = service.get_proposal(users[0], proposal.id, NOW)
    assert refreshed.status == "pending" and refreshed.my_decision == "accepted"
    assert "偷偷發現" in refreshed.invitation_text


def test_matching_policy_change_invalidates_old_invitation(service):
    users, _ = setup_group(service, ("need", "offer"))
    proposal = service.run_matches(users[0], 1, NOW)[0]
    service.put_agent(
        users[0],
        AgentInput(
            enabled=True,
            exploration="balanced",
            expected_version=1,
            quiet_start_hour=None,
            quiet_end_hour=None,
        ),
        NOW,
    )
    assert service.get_agent(users[0]).policy_version == 2
    assert service.get_proposal(users[0], proposal.id, NOW).status == "unavailable"


def test_decline_reason_is_private_and_does_not_rewrite_agent_preferences(service):
    users, _ = setup_group(service, ("need", "offer"))
    original = service.get_agent(users[1])
    proposal = service.run_matches(users[0], 1, NOW)[0]
    mine = service.decide(users[1], proposal.id, "declined", NOW, "privacy")
    assert mine.my_decline_reason == "privacy"
    theirs = service.get_proposal(users[0], proposal.id, NOW)
    assert theirs.my_decline_reason is None
    assert "privacy" not in theirs.model_dump_json()
    assert "已失效" in theirs.invitation_text
    assert service.get_agent(users[1]) == original
    assert service.decide(users[1], proposal.id, "declined", NOW, "privacy") == mine
    with pytest.raises(StealthError, match="decision_payload_conflict"):
        service.decide(users[1], proposal.id, "declined", NOW, "food")


def test_block_and_unblock_are_private_idempotent_and_do_not_revive_old_invites(service):
    users, _ = setup_group(service, ("need", "offer"))
    proposal = service.run_matches(users[0], 1, NOW)[0]
    service.block_user(users[0], users[1], NOW)
    service.block_user(users[0], users[1], NOW)
    assert service.get_proposal(users[1], proposal.id, NOW).status == "unavailable"
    assert service.run_matches(users[1], 1, NOW + timedelta(seconds=31)) == []
    service.unblock_user(users[0], users[1], NOW)
    service.unblock_user(users[0], users[1], NOW)
    assert service.get_proposal(users[0], proposal.id, NOW).status == "unavailable"
    with pytest.raises(StealthError, match="cannot_block_self"):
        service.block_user(users[0], users[0], NOW)
