"""One-command synthetic competition rehearsal; disposable DB, no Supabase or paid AI."""

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PERSONAS = [
    {
        "kind": "need",
        "text": (
            "政大正門，我想吃熱湯，預算120元，費用未知，"
            "60分鐘後開始，持續120分鐘，我願意下單和分食。"
        ),
        "roles": ["order", "share"],
    },
    {
        "kind": "offer",
        "text": (
            "我有熱湯可以分享，政大正門，每人80元，預算120元，"
            "60分鐘後開始，持續120分鐘，我願意分食。"
        ),
        "roles": ["share"],
    },
    {
        "kind": "plan",
        "text": (
            "60分鐘後會去政大正門，持續120分鐘，我願意順路取餐，預算120元，餐點熱湯，費用未知。"
        ),
        "roles": ["pickup"],
        "clarification": "餐點分類是正餐。",
    },
]


def run(run_local=False):
    from app.stealth.schemas import AgentInput, FormationInput, SignalInput
    from app.stealth.service import StealthError, StealthService
    from app.stealth.storage import LocalStore

    report = {
        "scope": "synthetic disposable local prototype; not App/Supabase acceptance",
        "model_called": run_local,
        "people": PERSONAS,
        "intake": [],
        "intake_initial": [],
    }
    if run_local:
        from app.core_cloud.intake import IntakeRequest, interpret
        from app.stealth.ollama import OllamaDraftProvider

        provider = OllamaDraftProvider("qwen3.5:4b", local_only_confirmed=True)
        for person in PERSONAS:
            result = interpret(IntakeRequest(turns=[person["text"]]), provider)
            report["intake_initial"].append(result.model_dump(mode="json"))
            if person.get("clarification") and any(q.field == "category" for q in result.questions):
                result = interpret(
                    IntakeRequest(
                        turns=[person["text"], person["clarification"]], previous=result.facts
                    ),
                    provider,
                )
            report["intake"].append(result.model_dump(mode="json"))
        # These outputs are evidence only, never silently treated as human confirmation.
    state = ROOT / ".local"
    state.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="competition-", dir=state) as folder:
        service = StealthService(LocalStore(Path(folder) / "synthetic.sqlite3"))
        now = datetime.now(UTC)
        users = [uuid4() for _ in PERSONAS]
        for user, person in zip(users, PERSONAS, strict=True):
            service.put_agent(
                user, AgentInput(enabled=True, quiet_start_hour=None, quiet_end_hour=None), now
            )
            service.create_signal(
                user,
                SignalInput(
                    kind=person["kind"],
                    category="meal",
                    title=person["text"],
                    location_id="main_gate",
                    starts_at=now + timedelta(hours=1),
                    ends_at=now + timedelta(hours=3),
                    expires_at=now + timedelta(minutes=30),
                    budget_max_twd=120,
                    known_cost_twd=80 if person["kind"] == "offer" else None,
                    tags=["熱湯"],
                    roles=person["roles"],
                ),
                now,
            )
        proposal = service.run_matches(users[0], 1, now)[0]
        assert proposal.member_count == 3
        terms = FormationInput(
            content="合成競賽展示：正門三人熱湯，依確認分工協調。",
            deadline_at=now + timedelta(minutes=20),
            confirmed_cost_twd=80,
            confirmed_roles={"order": True, "pickup": True},
            host_roles=["order"],
            acknowledge_unverified_details=True,
        )
        try:
            service.form(users[0], proposal.id, terms, now)
        except StealthError as error:
            assert str(error) == "proposal_not_ready"
        else:
            raise AssertionError("Missing participant consent was bypassed")
        for user in users:
            service.decide(user, proposal.id, "accepted", now)
        formed = service.form(users[0], proposal.id, terms, now)
        replay = service.form(users[0], proposal.id, terms, now)
        assert formed.id == replay.id
        report.update(
            members=3,
            cost_per_person=80,
            total_cost=240,
            without_consent="blocked",
            repeated_formation="same_id",
            storage=formed.storage,
            confirmation="scripted synthetic personas; real App requires each person",
        )
    (state / "competition-demo-latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-local",
        action="store_true",
        help="Explicitly call installed free loopback model for synthetic interview evidence",
    )
    args = parser.parse_args()
    result = run(args.run_local)
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in ("people", "intake", "intake_initial")
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("Report: .local/competition-demo-latest.json; temporary database removed")
