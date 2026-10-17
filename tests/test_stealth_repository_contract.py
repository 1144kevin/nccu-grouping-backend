from uuid import uuid4

from app.models.stealth import StealthAgent, StealthSignal
from app.stealth.postgres import agent_from_row, signal_from_row
from app.stealth.schemas import AgentInput
from app.stealth.service import StealthService
from app.stealth.storage import LocalStore
from tests.test_stealth import NOW, signal_input


def test_postgres_and_local_signal_domain_contract_agree(tmp_path):
    actor = uuid4()
    service = StealthService(LocalStore(tmp_path / "contract.sqlite3"))
    service.put_agent(actor, AgentInput(), NOW)
    value = signal_input(category="meal", acceptable_categories=["pizza", "bakery"])
    local = service.create_signal(actor, value, NOW)
    row = StealthSignal(
        id=local.id,
        owner_id=actor,
        kind=value.kind.value,
        category=value.category,
        title=value.title,
        location_id=value.location_id,
        starts_at=value.starts_at,
        ends_at=value.ends_at,
        expires_at=value.expires_at,
        budget_max_twd=value.budget_max_twd,
        known_cost_twd=value.known_cost_twd,
        attributes={
            "tags": value.tags,
            "roles": value.roles,
            "acceptable_categories": value.acceptable_categories,
            "dietary_requirements": value.dietary_requirements,
            "dietary_verified": value.dietary_verified,
        },
        version=local.version,
        status=local.status.value,
        created_at=NOW,
        updated_at=NOW,
    )
    assert signal_from_row(row) == local


def test_postgres_and_local_agent_domain_contract_agree(tmp_path):
    actor = uuid4()
    service = StealthService(LocalStore(tmp_path / "agent-contract.sqlite3"))
    value = AgentInput(enabled=True, tone="playful", avatar_key="cat", exploration="balanced")
    local = service.put_agent(actor, value, NOW)
    row = StealthAgent(
        user_id=actor,
        name=value.name,
        enabled=value.enabled,
        daily_invite_limit=value.daily_invite_limit,
        timezone=value.timezone,
        settings={
            "quiet_start_hour": value.quiet_start_hour,
            "quiet_end_hour": value.quiet_end_hour,
            "exploration": value.exploration,
            "tone": value.tone,
            "avatar_key": value.avatar_key,
        },
        version=local.version,
        policy_version=local.policy_version,
        created_at=NOW,
        updated_at=NOW,
    )
    assert agent_from_row(row) == local
