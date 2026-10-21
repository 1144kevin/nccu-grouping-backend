"""Analyze fixture-only iPhone results on Windows or Mac; never calls a model or database."""

import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402

from app.core_cloud.intake import IntakeFacts, ground, retain_supported_facts, review  # noqa: E402

FIXTURES = Path(__file__).with_name("Fixtures.json")


def differences(case, facts, questions=None):
    failures = [
        f"facts.{field}"
        for field, expected in case.get("facts", {}).items()
        if facts.get(field) != expected
    ]
    failures += [f"missing.role.{r}" for r in case.get("roles", []) if r not in facts["roles"]]
    failures += [
        f"forbidden.role.{r}" for r in case.get("forbidden_roles", []) if r in facts["roles"]
    ]
    if questions is not None:
        failures += [
            f"missing.question.{q}" for q in case.get("questions", []) if q not in questions
        ]
    return failures


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def analyze(report, fixture_path=FIXTURES):
    fixture_bytes = fixture_path.read_bytes()
    fixtures = json.loads(fixture_bytes)
    if report.get("format") != "apple-ai-probe-v1":
        raise ValueError("Unknown probe report format")
    if report.get("fixture_sha256") != hashlib.sha256(fixture_bytes).hexdigest():
        raise ValueError("Fixture checksum mismatch; do not score against different cases")
    if type(report.get("simulator")) is not bool:
        raise ValueError("Report must explicitly identify simulator vs physical device")
    if not isinstance(report.get("intake_runs"), list) or not isinstance(
        report.get("embedding_runs"), list
    ):
        raise ValueError("Report run lists missing")

    expected_rounds = {
        (c["id"], i + 1) for c in fixtures["intake"] for i in range(len(c["turns"]))
    }
    runs = {}
    for run in report["intake_runs"]:
        key = (run.get("case_id"), run.get("round"))
        if type(key[1]) is not int or key not in expected_rounds or key in runs:
            raise ValueError("Unknown or repeated intake round")
        if not finite_number(run.get("elapsed_ms")) or run["elapsed_ms"] < 0:
            raise ValueError("Invalid intake timing")
        runs[key] = run

    intake_results = []
    for case in fixtures["intake"]:
        previous = None
        final_raw = final_grounded = None
        errors = []
        warnings = []
        for index in range(len(case["turns"])):
            run = runs.get((case["id"], index + 1))
            if run is None or run.get("error") or not isinstance(run.get("facts"), dict):
                errors.append(f"round.{index + 1}.not_completed")
                continue
            try:
                raw = IntakeFacts.model_validate(run["facts"])
                checked = raw.model_copy(deep=True)
                turns = case["turns"][: index + 1]
                original = "\n".join(turns)
                warnings += ground(checked, original)
                if previous is not None and len(turns) > 1:
                    checked = retain_supported_facts(checked, previous, turns)
                    warnings += ground(checked, original)
                previous = checked
                if index == len(case["turns"]) - 1:
                    final_raw, final_grounded = raw, checked
            except (ValidationError, ValueError):
                errors.append(f"round.{index + 1}.invalid_schema")
        raw_failures = list(errors)
        grounded_failures = list(errors)
        if final_raw is not None:
            raw_failures += differences(case, final_raw.model_dump())
        else:
            raw_failures.append("no_final_output")
        if final_grounded is not None:
            result = review(final_grounded, "apple_on_device_probe")
            grounded_failures += differences(
                case, final_grounded.model_dump(), [q.field for q in result.questions]
            )
        else:
            grounded_failures.append("no_final_output")
        intake_results.append(
            {
                "id": case["id"],
                "raw_failures": sorted(set(raw_failures)),
                "grounded_failures": sorted(set(grounded_failures)),
                "grounding_warnings": list(dict.fromkeys(warnings)),
            }
        )

    expected_retrieval = {c["id"]: c for c in fixtures["retrieval"]}
    embedding_results = []
    for run in report["embedding_runs"]:
        failures = []
        if run.get("error"):
            failures.append(str(run["error"]))
        dims = run.get("dimensions")
        if type(dims) is not int or not 16 <= dims <= 4096:
            failures.append("invalid_or_unavailable_dimensions")
        if not finite_number(run.get("elapsed_ms")) or run["elapsed_ms"] < 0:
            failures.append("invalid_timing")
        results = {}
        for row in run.get("cases", []):
            key = row.get("case_id")
            if key not in expected_retrieval or key in results:
                raise ValueError("Unknown or repeated retrieval case")
            case = expected_retrieval[key]
            scores, vector = row.get("similarities", []), row.get("query_vector", [])
            invalid = (
                row.get("error")
                or len(scores) != len(case["documents"])
                or any(not finite_number(s) or abs(s) > 1.000001 for s in scores)
                or len(vector) != dims
                or any(not finite_number(v) for v in vector)
                or abs(sum(v * v for v in vector) - 1) > 1e-5
            )
            if invalid:
                results[key] = {"passed": False, "error": "invalid_or_failed_vector"}
                continue
            order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
            tied = abs(scores[order[0]] - scores[order[1]]) < 1e-9
            results[key] = {
                "passed": order[0] == case["expected"] and not tied,
                "top_index": order[0],
                "expected_index": case["expected"],
                "top_margin": round(scores[order[0]] - scores[order[1]], 6),
                "tied": tied,
            }
        missing = sorted(set(expected_retrieval) - set(results))
        if missing:
            failures.append("incomplete_cases")
        embedding_results.append(
            {
                "model_id": run.get("model_id"),
                "dimensions": dims,
                "database_dimension_compatible": dims == 512,
                "elapsed_ms": run.get("elapsed_ms"),
                "passed": sum(row["passed"] for row in results.values()),
                "total": len(expected_retrieval),
                "missing": missing,
                "failures": failures,
                "cases": results,
            }
        )

    times = [r["elapsed_ms"] for r in runs.values() if r.get("facts") and not r.get("error")]
    candidate = (
        not report["simulator"]
        and report.get("model_availability") == "available"
        and len(runs) == len(expected_rounds)
        and all(not r["raw_failures"] and not r["grounded_failures"] for r in intake_results)
        and any(
            not r["failures"] and r["database_dimension_compatible"] and r["passed"] >= 9
            for r in embedding_results
        )
    )
    quality_embeddings = [
        r for r in embedding_results if not r["failures"] and r["passed"] >= 9
    ]
    return {
        "format": "apple-ai-probe-analysis-v1",
        "physical_device_reported": not report["simulator"],
        "os": report.get("os"),
        "device_model": report.get("device_model"),
        "model_availability": report.get("model_availability"),
        "intake_protocol": report.get("intake_protocol", "v1-instructions-only"),
        "intake": {
            "recorded_rounds": len(runs),
            "expected_rounds": len(expected_rounds),
            "raw_passed": sum(not r["raw_failures"] for r in intake_results),
            "grounded_passed": sum(not r["grounded_failures"] for r in intake_results),
            "total": len(intake_results),
            "median_ms": round(statistics.median(times), 2) if times else None,
            "first_recorded_ms": times[0] if times else None,
            "cases": intake_results,
        },
        "embeddings": embedding_results,
        "embedding_quality_candidate": bool(quality_embeddings),
        "quality_candidate_dimensions_requiring_migration": sorted(
            {r["dimensions"] for r in quality_embeddings if r["dimensions"] != 512}
        ),
        "next_step": "candidate_for_app_integration" if candidate else "not_yet_verified",
        "limitations": [
            "Known regression fixtures, not a blind accuracy benchmark.",
            "Preliminary gate: all six raw/grounded intake cases and >=9/11 retrieval on 512D.",
            "A model configuration must be evaluated separately; vector spaces never mix.",
            "This does not verify cross-device vector compatibility, Supabase or App consent.",
            "Total physical RAM is recorded, not model memory usage or a memory limit.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.report.stat().st_size > 5 * 1024 * 1024:
        raise SystemExit("Report exceeds 5 MiB")
    result = analyze(json.loads(args.report.read_text(encoding="utf-8")))
    output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
