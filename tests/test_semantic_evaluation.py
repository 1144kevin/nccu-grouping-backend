import json

from app.stealth.provider_contracts import CandidateContext
from scripts.evaluate_semantic import FIXTURES


def test_semantic_fixture_file_has_deterministic_valid_expectations():
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert len(cases["retrieval"]) >= 12
    assert len({c["id"] for c in cases["retrieval"]}) == len(cases["retrieval"])
    for case in cases["retrieval"]:
        assert 0 <= case["expected"] < len(case["documents"])
    for case in cases["review"]:
        CandidateContext(key=case["id"], document=case["document"], facts=case["facts"])
