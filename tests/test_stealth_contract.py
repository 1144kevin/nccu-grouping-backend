import json
from pathlib import Path

from app.main import app
from scripts.export_stealth_openapi import build_stealth_contract


def test_committed_contract_matches_running_application():
    committed = json.loads(Path("docs/stealth.openapi.json").read_text(encoding="utf-8"))
    assert committed == build_stealth_contract()


def test_export_does_not_mutate_full_application_schema():
    original = json.dumps(app.openapi(), sort_keys=True)
    contract = build_stealth_contract()
    assert "/health" not in contract["paths"]
    assert "/api/v1/stealth-cloud/agent" not in contract["paths"]
    assert "/health" in app.openapi()["paths"]
    assert json.dumps(app.openapi(), sort_keys=True) == original


def test_every_stealth_operation_requires_bearer_authentication():
    schema = build_stealth_contract()
    operations = [
        operation
        for item in schema["paths"].values()
        for method, operation in item.items()
        if method in ("get", "post", "put", "patch", "delete")
    ]
    assert len(operations) >= 13
    assert all(operation["security"] == [{"HTTPBearer": []}] for operation in operations)
    parameters = schema["paths"]["/api/v1/stealth/signals"]["post"]["parameters"]
    assert any(p["name"] == "Idempotency-Key" and p["required"] for p in parameters)
