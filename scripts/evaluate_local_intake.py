"""Explicit real-loopback evaluation of synthetic fixtures; never uses Supabase."""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core_cloud.intake import IntakeRequest, interpret  # noqa: E402
from app.stealth.ollama import LocalModelError, OllamaDraftProvider  # noqa: E402


def check(case, result):
    facts = result["facts"]
    failures = [
        f"facts.{field}" for field, value in case.get("facts", {}).items() if facts[field] != value
    ]
    for key, actual in (
        ("roles", facts["roles"]),
        ("alternatives", facts["acceptable_categories"]),
        ("questions", [item["field"] for item in result["questions"]]),
    ):
        failures.extend(
            f"missing.{key}.{value}" for value in case.get(key, []) if value not in actual
        )
    failures.extend(
        f"forbidden.roles.{value}"
        for value in case.get("forbidden_roles", [])
        if value in facts["roles"]
    )
    if any(key in result for key in ("consent", "formed_post_id", "signal_id")):
        failures.append("unexpected_action")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--suite", choices=("standard", "challenge", "blind"), default="standard")
    parser.add_argument("--case", action="append", help="Run only the named synthetic case(s)")
    parser.add_argument("--output", default=".local/evals/stealth-intake-latest.json")
    options = parser.parse_args()
    output = (ROOT / options.output).resolve()
    if not output.is_relative_to(ROOT / ".local"):
        raise SystemExit("Evaluation output must stay in this project's ignored .local directory.")
    cases = json.loads(
        (
            ROOT
            / "tests/fixtures"
            / (
                {
                    "standard": "stealth_intake_eval.json",
                    "challenge": "stealth_intake_challenge.json",
                    "blind": "stealth_intake_blind.json",
                }[options.suite]
            )
        ).read_text(encoding="utf-8")
    )
    if options.case:
        cases = [case for case in cases if case["id"] in options.case]
    if not cases:
        raise SystemExit("No matching synthetic cases.")
    provider = OllamaDraftProvider(options.model, local_only_confirmed=True)
    results = []
    for case in cases:
        started = time.monotonic()
        try:
            previous = None
            rounds = []
            for index in range(len(case["turns"])):
                parsed = interpret(
                    IntakeRequest(turns=case["turns"][: index + 1], previous=previous), provider
                )
                previous = parsed.facts
                rounds.append(parsed.model_dump(mode="json"))
            result = rounds[-1]
            failures = check(case, result)
        except LocalModelError as error:
            result = None
            failures = [str(error)]
        seconds = round(time.monotonic() - started, 2)
        results.append(
            {
                "id": case["id"],
                "seconds": seconds,
                "passed": not failures,
                "failures": failures,
                "result": result,
                "rounds": rounds if result else [],
            }
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps({"model": options.model, "cases": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            f"{case['id']}: {'PASS' if not failures else 'FAIL'} ({seconds}s) {','.join(failures)}",
            flush=True,
        )
    passed = sum(row["passed"] for row in results)
    median = statistics.median(row["seconds"] for row in results)
    print(
        f"Synthetic case checks: {passed}/{len(results)}; median {median:.2f}s. "
        "Not a real-user accuracy estimate."
    )
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
