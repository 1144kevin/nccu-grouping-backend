from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import get_settings
from app.main import app
from app.stealth.dependencies import get_local_service, local_service
from app.stealth.matching import build_candidate
from app.stealth.schemas import (
    Agent,
    AgentInput,
    FormationInput,
    Signal,
    SignalInput,
)
from app.stealth.service import StealthError, StealthService
from app.stealth.storage import LocalStore

NOW = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)  # 16:00 Taipei


def signal_input(kind="need", **changes):
    values = {
        "kind": kind,
        "category": "pizza",
        "title": "private raw text",
        "location_id": "east_gate",
        "starts_at": NOW + timedelta(hours=1),
        "ends_at": NOW + timedelta(hours=2),
        "expires_at": NOW + timedelta(minutes=50),
        "budget_max_twd": 150,
        "tags": ["dinner"],
        "roles": {"need": ["share"], "offer": ["coupon"], "plan": ["pickup"]}[kind],
    }
    values.update(changes)
    return SignalInput(**values)


def formation_input(**changes):
    values = {
        "content": "Confirmed pizza group for three people",
        "deadline_at": NOW + timedelta(minutes=20),
        "confirmed_cost_twd": 130,
        "confirmed_roles": {"order": True, "pickup": True},
        "host_roles": ["order", "pickup"],
        "acknowledge_unverified_details": True,
    }
    values.update(changes)
    return FormationInput(**values)


@pytest.fixture
def service(tmp_path):
    return StealthService(LocalStore(tmp_path / "stealth.sqlite3"))


def setup_group(service, kinds=("need", "offer", "plan")):
    users, signals = [], []
    for kind in kinds:
        user = uuid4()
        service.put_agent(
            user, AgentInput(enabled=True, quiet_start_hour=None, quiet_end_hour=None), NOW
        )
        signals.append(service.create_signal(user, signal_input(kind), NOW))
        users.append(user)
    return users, signals


def accept_all(service, users, proposal_id):
    for user in users:
        service.decide(user, proposal_id, "accepted", NOW)


def test_three_way_complementary_match(service):
    users, _ = setup_group(service)
    matches = service.run_matches(users[0], 1, NOW)
    assert matches[0].member_count == 3
    assert matches[0].budget_max_twd == 150
    assert "有人明確表示願意順路取餐" in matches[0].reasons
    assert matches[0].status == "pending"


@pytest.mark.parametrize(
    "changed",
    [
        {"location_id": "main_gate"},
        {"category": "drink"},
        {"starts_at": NOW + timedelta(hours=3), "ends_at": NOW + timedelta(hours=4)},
        {"known_cost_twd": 151},
        {"dietary_requirements": ["no_beef"]},
    ],
)
def test_hard_constraint_mismatch(service, changed):
    users, signals = setup_group(service, ("need", "offer"))
    other = Signal(
        **signal_input("offer", **changed).model_dump(),
        id=signals[1].id,
        owner_id=users[1],
        created_at=NOW,
    )
    agents = {u: service.get_agent(u) for u in users}
    assert build_candidate([signals[0], other], agents, set(), NOW) is None


def test_no_need_no_match(service):
    users, _ = setup_group(service, ("offer", "plan"))
    assert service.run_matches(users[0], 3, NOW) == []


def test_same_person_cannot_fill_two_seats(service):
    users, signals = setup_group(service, ("need", "offer"))
    other = signals[1].model_copy(update={"owner_id": users[0]})
    assert (
        build_candidate([signals[0], other], {users[0]: service.get_agent(users[0])}, set(), NOW)
        is None
    )


def test_block_in_either_direction_prevents_match(service):
    users, _ = setup_group(service, ("need", "offer"))
    with service.store.transaction() as unit:
        unit.connection.execute(
            "INSERT INTO local_blocks VALUES (?,?)", (str(users[1]), str(users[0]))
        )
    assert service.run_matches(users[0], 3, NOW) == []


def test_quiet_hours_handle_overnight_window():
    agent = Agent(user_id=uuid4(), version=1, updated_at=NOW, enabled=True)
    assert not agent.quiet_at(NOW)
    assert agent.quiet_at(NOW.replace(hour=15))  # 23:00 Taipei
    assert agent.quiet_at(NOW.replace(hour=23))  # 07:00 Taipei next day
    assert not agent.quiet_at(NOW.replace(hour=0))  # 08:00 Taipei


def test_default_opt_out_and_pause_invalidates(service):
    users, _ = setup_group(service, ("need", "offer"))
    proposal = service.run_matches(users[0], 1, NOW)[0]
    service.put_agent(users[1], AgentInput(expected_version=1), NOW)
    assert service.get_proposal(users[0], proposal.id, NOW).status == "unavailable"
    with pytest.raises(StealthError, match="proposal_unavailable"):
        service.decide(users[0], proposal.id, "accepted", NOW)


def test_raw_signals_are_private_and_proposal_omits_identities(service):
    users, signals = setup_group(service, ("need", "offer"))
    with pytest.raises(StealthError) as exc:
        service.get_signal(users[0], signals[1].id)
    assert exc.value.status == 404
    proposal = service.run_matches(users[0], 1, NOW)[0]
    serialized = proposal.model_dump_json()
    for forbidden in [str(users[1]), str(signals[1].id), "private raw text", "fingerprint"]:
        assert forbidden not in serialized
    with pytest.raises(StealthError) as exc:
        service.get_proposal(uuid4(), proposal.id, NOW)
    assert exc.value.status == 404


def test_cooldown_and_no_duplicate_invites(service):
    users, _ = setup_group(service, ("need", "offer"))
    first = service.run_matches(users[0], 1, NOW)
    with pytest.raises(StealthError) as exc:
        service.run_matches(users[0], 1, NOW)
    assert exc.value.status == 429
    assert service.run_matches(users[1], 1, NOW + timedelta(seconds=31)) == []
    assert len(first) == 1


def test_matching_is_not_capped_by_daily_invite_limit(service):
    users, _ = setup_group(service)
    for user in users:
        service.put_agent(
            user,
            AgentInput(
                enabled=True,
                daily_invite_limit=1,
                expected_version=1,
                quiet_start_hour=None,
                quiet_end_hour=None,
            ),
            NOW,
        )
    assert len(service.run_matches(users[0], 5, NOW)) == 3


def test_signal_in_ready_chat_cannot_match_another_signal(service):
    users, _ = setup_group(service)
    proposals = service.run_matches(users[0], 5, NOW)
    first = proposals[0]
    accepted_users = set()
    for u in users:
        try:
            view = service.decide(u, first.id, "accepted", None, NOW)
            accepted_users.add(u)
            if view.status == "ready":
                break
        except StealthError:
            pass
    other = next(u for u in users if u not in accepted_users)
    assert service.run_matches(other, 5, NOW + timedelta(seconds=31)) == []


def test_revoke_is_idempotent_and_invalidates(service):
    users, signals = setup_group(service, ("need", "offer"))
    proposal = service.run_matches(users[0], 1, NOW)[0]
    first = service.revoke_signal(users[1], signals[1].id, NOW)
    again = service.revoke_signal(users[1], signals[1].id, NOW)
    assert first.version == again.version == 2
    assert service.get_proposal(users[0], proposal.id, NOW).status == "unavailable"


def test_decline_hides_actor_and_is_final(service):
    users, _ = setup_group(service, ("need", "offer"))
    proposal = service.run_matches(users[0], 1, NOW)[0]
    service.decide(users[1], proposal.id, "declined", NOW)
    view = service.get_proposal(users[0], proposal.id, NOW)
    assert view.status == "unavailable" and view.my_decision == "pending"
    assert service.decide(users[1], proposal.id, "declined", NOW).status == "unavailable"
    with pytest.raises(StealthError):
        service.decide(users[1], proposal.id, "accepted", NOW)


def test_expiry_rechecked_before_acceptance(service):
    users, _ = setup_group(service, ("need", "offer"))
    proposal = service.run_matches(users[0], 1, NOW)[0]
    with pytest.raises(StealthError):
        service.decide(users[0], proposal.id, "accepted", proposal.expires_at)


def test_unanimous_consent_and_explicit_formation(service):
    users, signals = setup_group(service)
    proposal = service.run_matches(users[0], 1, NOW)[0]
    with pytest.raises(StealthError, match="proposal_not_ready"):
        service.form(users[0], proposal.id, formation_input(), NOW)
    accept_all(service, users, proposal.id)
    assert service.get_proposal(users[0], proposal.id, NOW).status == "ready"
    formation = service.form(users[0], proposal.id, formation_input(), NOW)
    assert formation.storage == "local_sqlite"
    assert service.form(users[0], proposal.id, formation_input(), NOW) == formation
    with service.store.transaction() as unit:
        assert unit.connection.execute("SELECT count(*) FROM local_posts").fetchone()[0] == 1
        assert (
            unit.connection.execute("SELECT count(*) FROM local_conversation_members").fetchone()[0]
            == 3
        )
    assert all(service.get_signal(s.owner_id, s.id).status == "consumed" for s in signals)


def test_over_budget_and_missing_roles_rejected(service):
    users, _ = setup_group(service, ("need", "offer"))
    proposal = service.run_matches(users[0], 1, NOW)[0]
    accept_all(service, users, proposal.id)
    with pytest.raises(StealthError, match="cost_exceeds_budget"):
        service.form(users[0], proposal.id, formation_input(confirmed_cost_twd=151), NOW)
    with pytest.raises(StealthError, match="confirm_order_and_pickup_roles"):
        service.form(users[0], proposal.id, formation_input(confirmed_roles={}), NOW)


def test_competing_formations_consume_signal_only_once(service):
    users, _ = setup_group(service)
    proposals = service.run_matches(users[0], 3, NOW)
    assert len(proposals) >= 2
    for proposal in proposals:
        with service.store.transaction() as unit:
            members = unit.get("proposals", proposal.id).members
        accept_all(service, [m.user_id for m in members], proposal.id)

    def form(proposal):
        try:
            return service.form(users[0], proposal.id, formation_input(), NOW)
        except StealthError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(form, proposals[:2]))
    assert sum(item is not None for item in results) == 1


def test_persistence_after_reopen(service):
    users, _ = setup_group(service)
    reopened = StealthService(LocalStore(service.store.path))
    assert reopened.get_agent(users[0]).enabled


def test_pagination_bound_to_owner_and_table(service):
    users, _ = setup_group(service, ("need", "offer"))
    for _ in range(2):
        service.create_signal(users[0], signal_input(), NOW)
    page, cursor = service.list_items(users[0], "signals", 1, None, NOW)
    assert len(page) == 1 and cursor
    next_page, _ = service.list_items(users[0], "signals", 1, cursor, NOW)
    assert page[0].id != next_page[0].id
    with pytest.raises(StealthError, match="invalid_cursor"):
        service.list_items(users[1], "signals", 1, cursor, NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"starts_at": "2026-10-15T18:00:00"},
        {"budget_max_twd": -1},
        {"budget_max_twd": True},
        {"ends_at": NOW},
        {"owner_id": str(uuid4())},
    ],
)
def test_input_rejects_invalid_or_spoofed_data(changes):
    with pytest.raises(ValidationError):
        signal_input(**changes)


def test_api_disabled_by_default_and_health_retained():
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/stealth/agent").status_code == 503


def test_api_local_authentication_and_origin_guard(service, monkeypatch):
    monkeypatch.setenv("STEALTH_LOCAL_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT", "local")
    get_settings.cache_clear()
    app.dependency_overrides[get_local_service] = lambda: service
    actor, token = uuid4(), "test-only-credential-" + "x" * 32
    with service.store.transaction() as unit:
        unit.add_local_user(actor, token)
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/stealth/agent").status_code == 401
            headers = {"Authorization": f"Bearer {token}"}
            response = client.put("/api/v1/stealth/agent", json={"enabled": True}, headers=headers)
            assert response.status_code == 200
            assert response.json()["user_id"] == str(actor)
            assert (
                client.get(
                    "/api/v1/stealth/agent",
                    headers={
                        "Authorization": "Bearer " + "wrong" * 10,
                    },
                ).status_code
                == 401
            )
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        local_service.cache_clear()
