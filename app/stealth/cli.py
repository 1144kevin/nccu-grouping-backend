"""Local synthetic demonstration. Does not bind ports or call any external service."""

import argparse
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from app.stealth.schemas import AgentInput, FormationInput, SignalInput
from app.stealth.service import StealthService
from app.stealth.storage import LocalStore

ROOT = Path(__file__).resolve().parents[2]


def demo() -> dict:
    now = datetime.now(UTC)
    with TemporaryDirectory(prefix="stealth-demo-") as directory:
        service = StealthService(LocalStore(Path(directory) / "synthetic.sqlite3"))
        users = [uuid4() for _ in range(3)]
        for user, kind, roles in zip(
            users,
            ("need", "offer", "plan"),
            (["share", "order"], ["coupon"], ["pickup"]),
            strict=True,
        ):
            service.put_agent(
                user,
                AgentInput(
                    enabled=True,
                    quiet_start_hour=None,
                    quiet_end_hour=None,
                ),
                now,
            )
            service.create_signal(
                user,
                SignalInput(
                    kind=kind,
                    category="pizza",
                    title=f"Synthetic {kind} signal",
                    location_id="east_gate",
                    starts_at=now + timedelta(hours=1),
                    ends_at=now + timedelta(hours=2),
                    expires_at=now + timedelta(minutes=50),
                    budget_max_twd=150,
                    roles=roles,
                ),
                now,
            )
        proposal = service.run_matches(users[0], 1, now)[0]
        for user in users:
            service.decide(user, proposal.id, "accepted", now)
        formation = service.form(
            users[0],
            proposal.id,
            FormationInput(
                content="Synthetic pizza group confirmed by all three participants",
                deadline_at=now + timedelta(minutes=20),
                confirmed_cost_twd=130,
                confirmed_roles={"order": True, "pickup": True},
                host_roles=["order"],
                acknowledge_unverified_details=True,
            ),
            now,
        )
        with service.store.transaction() as unit:
            posts = unit.connection.execute("SELECT count(*) FROM local_posts").fetchone()[0]
            members = unit.connection.execute(
                "SELECT count(*) FROM local_conversation_members",
            ).fetchone()[0]
        return {
            "provider": "constraint_rules_v1",
            "external_calls": 0,
            "proposal_member_count": proposal.member_count,
            "reasons": proposal.reasons,
            "unknowns": proposal.unknowns,
            "final_status": service.get_proposal(users[0], proposal.id, now).status,
            "storage": formation.storage,
            "local_posts": posts,
            "chat_members": members,
        }


def init_local_users() -> None:
    """Explicit CLI opt-in. Credentials are synthetic and saved only in gitignored .local."""
    local = ROOT / ".local"
    local.mkdir(exist_ok=True)
    credential_path = local / "stealth-dev-users.json"
    # Exclusive creation avoids overwriting prior credentials or resetting existing data.
    with credential_path.open("x", encoding="utf-8") as output:
        store = LocalStore(local / "stealth.sqlite3")
        credentials = []
        with store.transaction() as unit:
            for label in ("Synthetic A", "Synthetic B", "Synthetic C"):
                user, token = uuid4(), secrets.token_urlsafe(48)
                unit.add_local_user(user, token)
                credentials.append({"label": label, "user_id": str(user), "token": token})
        json.dump(credentials, output, indent=2)
    print("Synthetic credentials saved in .local/stealth-dev-users.json; tokens not printed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("demo", "init-local-users", "dispatch"))
    args = parser.parse_args()
    if args.command == "demo":
        print(json.dumps(demo(), ensure_ascii=True, indent=2))
    elif args.command == "init-local-users":
        init_local_users()
    else:
        path = ROOT / ".local" / "stealth.sqlite3"
        if not path.is_file():
            parser.error("initialize synthetic local users first")
        service = StealthService(LocalStore(path))
        print(json.dumps({"attempted_jobs": service.dispatch_local_jobs()}))
