import hashlib
import json

import pytest

from tools.apple_ai_probe.analyze import FIXTURES, analyze


def report():
    return {
        "format": "apple-ai-probe-v1",
        "fixture_sha256": hashlib.sha256(FIXTURES.read_bytes()).hexdigest(),
        "simulator": False,
        "model_availability": "available",
        "intake_runs": [],
        "embedding_runs": [],
    }


def test_empty_or_simulator_report_never_claims_real_device_verification():
    value = report()
    result = analyze(value)
    assert result["next_step"] == "not_yet_verified"
    assert result["intake"]["raw_passed"] == 0
    value["simulator"] = True
    assert not analyze(value)["physical_device_reported"]


def test_changed_fixtures_or_duplicate_rounds_are_rejected():
    value = report()
    value["fixture_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checksum"):
        analyze(value)
    value = report()
    row = {"case_id": "budget_change", "round": 1, "elapsed_ms": 10, "error": "unavailable"}
    value["intake_runs"] = [row, row]
    with pytest.raises(ValueError, match="repeated"):
        analyze(value)


def test_grounding_correction_is_reported_separately_from_model_error():
    value = report()
    # Deliberately stale raw output: the real grounding code must repair it, but
    # the analyzer must still show that the model itself got the correction wrong.
    facts = {
        "title": "飲料需求",
        "kind": "need",
        "category": "drink",
        "location_id": "main_gate",
        "delay_minutes": 60,
        "duration_minutes": 60,
        "budget_max_twd": 150,
        "roles": ["share"],
        "roles_confirmed": True,
        "evidence": {
            "budget": "預算150元",
            "start": "一小時後開始",
            "duration": "持續一小時",
            "roles": "願意分食",
        },
    }
    value["intake_runs"] = [
        {"case_id": "budget_change", "round": i, "elapsed_ms": 10, "facts": facts}
        for i in (1, 2)
    ]
    result = analyze(value)
    case = next(c for c in result["intake"]["cases"] if c["id"] == "budget_change")
    assert "facts.budget_max_twd" in case["raw_failures"]
    assert not case["grounded_failures"]
    assert result["next_step"] == "not_yet_verified"


@pytest.mark.parametrize("vector", [[0] * 512, [float("nan")] * 512, [1], [True] * 512])
def test_invalid_vectors_never_count_as_retrieval_success(vector):
    value = report()
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))["retrieval"][0]
    value["embedding_runs"] = [
        {
            "model_id": "synthetic-tests-only",
            "dimensions": 512,
            "elapsed_ms": 1,
            "cases": [
                {"case_id": case["id"], "similarities": [0.1, 0.9, 0.2], "query_vector": vector}
            ],
        }
    ]
    result = analyze(value)
    assert result["embeddings"][0]["passed"] == 0
    assert result["next_step"] == "not_yet_verified"


def test_good_640d_retrieval_does_not_claim_existing_512d_database_compatibility():
    value = report()
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))["retrieval"]
    value["embedding_runs"] = [
        {
            "model_id": "synthetic-tests-only-640d",
            "dimensions": 640,
            "elapsed_ms": 1,
            "cases": [
                {
                    "case_id": case["id"],
                    "similarities": [0.9 if i == case["expected"] else 0.1 for i in range(3)],
                    "query_vector": [1] + [0] * 639,
                }
                for case in cases
            ],
        }
    ]
    result = analyze(value)
    assert result["embedding_quality_candidate"]
    assert result["quality_candidate_dimensions_requiring_migration"] == [640]
    assert not result["embeddings"][0]["database_dimension_compatible"]
    assert result["next_step"] == "not_yet_verified"  # No real intake run in this fixture.
