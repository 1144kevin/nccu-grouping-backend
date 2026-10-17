"""Export the local contract without starting a server or contacting a database."""

import json
from copy import deepcopy
from pathlib import Path

from app.core.openapi import prune_schema_components
from app.main import app


def build_stealth_contract() -> dict:
    schema = deepcopy(app.openapi())
    schema["paths"] = {
        path: value
        for path, value in schema["paths"].items()
        if path.startswith("/api/v1/stealth/")
    }
    prune_schema_components(schema)
    schema["info"]["description"] = (
        "Local-only prototype; disabled by default. Synthetic bearer credentials only. "
        "Offline rule provider, not a connected LLM. PostgreSQL integration pending."
    )
    schema["servers"] = [{"url": "http://127.0.0.1:8000"}]
    return schema


if __name__ == "__main__":
    schema = build_stealth_contract()
    target = Path(__file__).resolve().parents[1] / "docs" / "stealth.openapi.json"
    target.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(schema['paths'])} paths; no network calls.")
