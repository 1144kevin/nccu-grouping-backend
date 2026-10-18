"""Exercise real local embeddings/review with an isolated disposable synthetic SQLite group."""

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-local", action="store_true")
    if not parser.parse_args().run_local:
        print("Dry run. --run-local uses installed loopback models and a disposable synthetic DB.")
        return
    from app.stealth.local_providers import OllamaCandidateReviewer, OllamaEmbeddingProvider
    from app.stealth.ollama import OllamaDraftProvider
    from app.stealth.schemas import AgentInput, FormationInput, SignalInput
    from app.stealth.semantic import SemanticMatcher
    from app.stealth.semantic_local import run_local_semantic
    from app.stealth.service import StealthError, StealthService
    from app.stealth.storage import LocalStore

    matcher = SemanticMatcher(
        OllamaEmbeddingProvider("qwen3-embedding:0.6b", local_only_confirmed=True),
        OllamaCandidateReviewer(OllamaDraftProvider("qwen3.5:4b", local_only_confirmed=True)),
    )
    (ROOT / ".local").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="semantic-smoke-", dir=ROOT / ".local") as folder:
        service = StealthService(LocalStore(Path(folder) / "synthetic.sqlite3"))
        now = datetime.now(UTC)
        users = [uuid4(), uuid4()]
        for user, kind, title, roles in zip(
            users,
            ["need", "offer"],
            ["想喝熱湯，可以負責下單", "有熱湯可分享，也願意幫忙取餐"],
            [["order", "share"], ["pickup", "share"]],
            strict=True,
        ):
            service.put_agent(
                user, AgentInput(enabled=True, quiet_start_hour=None, quiet_end_hour=None), now
            )
            service.create_signal(
                user,
                SignalInput(
                    kind=kind,
                    category="meal",
                    title=title,
                    location_id="east_gate",
                    starts_at=now + timedelta(hours=1),
                    ends_at=now + timedelta(hours=2),
                    expires_at=now + timedelta(minutes=50),
                    budget_max_twd=150,
                    roles=roles,
                ),
                now,
            )
        result = run_local_semantic(service, users[0], 1, matcher)
        assert result.ranking.status == "completed" and result.created == 1
        assert result.ranking.items[0].review_status == "completed"
        assert result.items[0].status == "pending"
        terms = FormationInput(
            content="這是隔離合成資料的熱湯測試團",
            deadline_at=now + timedelta(minutes=10),
            confirmed_cost_twd=90,
            confirmed_roles={"order": True, "pickup": True},
            host_roles=["order"],
            acknowledge_unverified_details=True,
        )
        try:
            service.form(users[0], result.items[0].id, terms, datetime.now(UTC))
        except StealthError as error:
            assert str(error) == "proposal_not_ready"
        else:
            raise AssertionError("Model bypassed participant consent")
        print(
            json.dumps(
                {
                    "created": result.created,
                    "status": result.items[0].status,
                    "review": result.ranking.items[0].review.model_dump(),
                    "formation_without_consent": "blocked",
                    "storage": "isolated disposable SQLite, no Supabase access",
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    print("Disposable synthetic database removed.")


if __name__ == "__main__":
    main()
