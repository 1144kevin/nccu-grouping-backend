from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import HTTPException

from app.core_cloud.stealth_watch import StealthWatch


def service(enabled=True, active=True):
    return SimpleNamespace(
        agent=Mock(return_value=SimpleNamespace(enabled=enabled)),
        signals=Mock(
            return_value=SimpleNamespace(
                items=[
                    SimpleNamespace(
                        status="active", expires_at=datetime.now(UTC) + timedelta(hours=1)
                    )
                ]
                if active
                else []
            )
        ),
        run=Mock(),
        store=SimpleNamespace(settings=SimpleNamespace(stealth_semantic_enabled=False)),
    )


def test_watch_finds_future_opportunities_without_another_client_request():
    watch = StealthWatch(interval=0)
    owner = service()
    watch.subscribe("owner", owner)
    watch.cycle()
    watch.cycle()
    assert owner.run.call_count == 2
    owner.run.assert_called_with(1)
    assert not hasattr(owner, "decide")  # Background work never accepts for its owner.


def test_watch_stops_on_logout_or_disabled_agent_and_respects_capacity():
    watch = StealthWatch(maximum=2)
    expired = service()
    expired.agent.side_effect = HTTPException(401, "session_expired")
    disabled = service(enabled=False)
    watch.subscribe("expired", expired)
    watch.subscribe("disabled", disabled)
    watch.subscribe("overflow", service())
    watch.cycle()
    assert watch.jobs == {}
    disabled.run.assert_not_called()


def test_polling_does_not_reset_next_run_and_new_session_replaces_old_token():
    watch = StealthWatch()
    first, refreshed = service(), service()
    watch.subscribe("owner", first)
    watch.cycle()
    due = watch.jobs["owner"].due
    watch.subscribe("owner", refreshed)
    assert watch.jobs["owner"].due == due
    watch.jobs["owner"].due = 0
    watch.cycle()
    assert refreshed.run.call_count == 1
    watch.subscribe("finished", service(active=False))
    watch.cycle()
    assert "finished" not in watch.jobs
