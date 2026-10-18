"""Explicit synthetic evaluation; loopback Ollama only, no account/database access."""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures" / "semantic_cases.json"


def main():
    from app.stealth.local_providers import OllamaCandidateReviewer, OllamaEmbeddingProvider
    from app.stealth.ollama import LocalModelError, OllamaDraftProvider
    from app.stealth.provider_contracts import CandidateContext
    from app.stealth.semantic import SemanticMatcher

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-local", action="store_true", help="Run installed free local models")
    args = parser.parse_args()
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
    if not args.run_local:
        print(f"Dry run: {len(cases['retrieval'])} retrieval, {len(cases['review'])} review cases.")
        print("Use --run-local to call installed models on 127.0.0.1:11434. No downloads.")
        return
    matcher = SemanticMatcher(
        OllamaEmbeddingProvider("qwen3-embedding:0.6b", local_only_confirmed=True)
    )
    reviewer = OllamaCandidateReviewer(OllamaDraftProvider("qwen3.5:4b", local_only_confirmed=True))
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "Fixed synthetic eligible retrieval candidates, not production accuracy",
        "retrieval": [],
        "review": [],
    }
    for case in cases["retrieval"]:
        start = time.perf_counter()
        contexts = [
            CandidateContext(key=str(i), document=text, facts=["category"])
            for i, text in enumerate(case["documents"])
        ]
        result = matcher.rank(case["query"], contexts, review_limit=0)
        expected = str(case["expected"])
        row = {
            "id": case["id"],
            "expected": expected,
            "passed": result.status == "completed" and result.items[0].key == expected,
            "top2": result.status == "completed" and expected in [i.key for i in result.items[:2]],
            "seconds": round(time.perf_counter() - start, 3),
            "ranking": result.model_dump(),
        }
        report["retrieval"].append(row)
        print(f"Retrieval {case['id']}: {'PASS' if row['passed'] else 'FAIL'}", flush=True)
    for case in cases["review"]:
        start = time.perf_counter()
        row = {"id": case["id"], "passed": False, "model": reviewer.model}
        try:
            context = CandidateContext(
                key=case["id"],
                query=case.get("query", ""),
                document=case["document"],
                facts=case["facts"],
            )
            result = reviewer.review(context)
            row.update(
                judgment=result.model_dump(),
                passed=(
                    result.verdict == case["expected_verdict"]
                    and bool(result.reason_codes)
                    and set(result.reason_codes) <= set(context.facts)
                ),
            )
        except LocalModelError as error:
            row["error"] = str(error)
        row["seconds"] = round(time.perf_counter() - start, 3)
        report["review"].append(row)
        print(f"Review {case['id']}: {'PASS' if row['passed'] else 'FAIL'}", flush=True)
    report["summary"] = {
        "retrieval_top1": sum(r["passed"] for r in report["retrieval"]),
        "retrieval_top2": sum(r["top2"] for r in report["retrieval"]),
        "retrieval_total": len(report["retrieval"]),
        "review_passed": sum(r["passed"] for r in report["review"]),
        "review_total": len(report["review"]),
    }
    target = ROOT / ".local" / "evals" / "semantic-latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    print(f"Synthetic report: {target}")
    return 0 if all(r["passed"] for group in ("retrieval", "review") for r in report[group]) else 1


if __name__ == "__main__":
    sys.exit(main())
