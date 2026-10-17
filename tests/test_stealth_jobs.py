from datetime import timedelta
from uuid import uuid4

import pytest

from app.stealth import service as service_module
from app.stealth.schemas import AgentInput
from app.stealth.service import StealthService
from app.stealth.storage import LocalStore
from tests.test_stealth import NOW, setup_group, signal_input


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "utc_now", lambda: NOW)
    return StealthService(LocalStore(tmp_path / "jobs.sqlite3"))


def test_jobs_survive_restart_and_dispatch_without_manual_matching(service):
    users, _ = setup_group(service)
    reopened = StealthService(LocalStore(service.store.path))
    assert reopened.dispatch_local_jobs() == 3
    page, _ = reopened.list_items(users[0], "proposals", 20, None, NOW)
    assert any(proposal.member_count == 3 for proposal in page)


def test_opt_out_retains_job_without_inviting_user(service):
    user = uuid4()
    service.put_agent(user, AgentInput(), NOW)
    service.create_signal(user, signal_input(), NOW)
    service.attempt_passive_matches(user)
    with service.store.transaction() as unit:
        assert unit.connection.execute("SELECT count(*) FROM match_jobs").fetchone()[0] == 1
        assert unit.all("proposals") == []


def test_quiet_hours_do_not_discard_pending_work(service):
    user = uuid4()
    service.put_agent(user, AgentInput(enabled=True, quiet_start_hour=15, quiet_end_hour=17), NOW)
    service.create_signal(user, signal_input(), NOW)
    service.attempt_passive_matches(user)
    with service.store.transaction() as unit:
        assert unit.connection.execute("SELECT count(*) FROM match_jobs").fetchone()[0] == 1


def test_new_job_generation_is_not_removed_by_older_callback(service, monkeypatch):
    users, _ = setup_group(service)
    original = service.run_matches

    def newer_signal(actor, maximum, now):
        result = original(actor, maximum, now)
        service.create_signal(actor, signal_input(title="New event after matching"), now)
        return result

    monkeypatch.setattr(service, "run_matches", newer_signal)
    service.attempt_passive_matches(users[0])
    with service.store.transaction() as unit:
        row = unit.connection.execute(
            "SELECT generation FROM match_jobs WHERE actor=?", (str(users[0]),)
        ).fetchone()
    assert row[0] == 2


def test_quota_resets_at_local_day_boundary(service):
    users, _ = setup_group(service, ("need", "offer"))
    # Quota was consumed today, but tomorrow's new signals may produce invitations.
    service.run_matches(users[0], 1, NOW)
    tomorrow = NOW + timedelta(days=1)
    for user in users:
        service.create_signal(
            user,
            signal_input(
                starts_at=tomorrow + timedelta(hours=1),
                ends_at=tomorrow + timedelta(hours=2),
                expires_at=tomorrow + timedelta(minutes=50),
            ),
            tomorrow,
        )
    assert service.run_matches(users[0], 1, tomorrow)


def test_retry_time_prevents_immediate_reprocessing(service):
    user = uuid4()
    service.put_agent(user, AgentInput(), NOW)
    service.create_signal(user, signal_input(), NOW)
    assert service.dispatch_local_jobs() == 1
    assert service.dispatch_local_jobs() == 0


def test_expired_signals_remove_only_local_matching_work(service, monkeypatch):
    users, _ = setup_group(service)
    monkeypatch.setattr(service_module, "utc_now", lambda: NOW + timedelta(hours=3))
    assert service.dispatch_local_jobs() == 3
    with service.store.transaction() as unit:
        assert unit.connection.execute("SELECT count(*) FROM match_jobs").fetchone()[0] == 0
        assert len(unit.all("signals")) == 3  # No user content was deleted.
