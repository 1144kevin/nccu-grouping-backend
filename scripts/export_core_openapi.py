"""Export the local login/grouping contract; no server, DB, or network access."""

import json
from copy import deepcopy
from pathlib import Path

from app.core.openapi import prune_schema_components
from app.main import app


def build_core_contract() -> dict:
    schema = deepcopy(app.openapi())
    schema["paths"] = {
        path: value
        for path, value in schema["paths"].items()
        if any(
            path.startswith(prefix)
            for prefix in (
                "/api/v1/auth/",
                "/api/v1/account/",
                "/api/v1/reports",
                "/api/v1/recommendations",
                "/api/v1/search",
                "/api/v1/uploads",
                "/api/v1/posts",
                "/api/v1/join-requests/",
                "/api/v1/development/demo",
                "/api/v1/development/clock",
                "/api/v1/development/readiness",
                "/api/v1/conversations",
                "/api/v1/users/",
                "/api/v1/notifications",
                "/api/v1/stealth-cloud",
            )
        )
    }
    prune_schema_components(schema)
    schema["info"]["description"] = (
        "Local FastAPI login/grouping API with separate SQLite and Supabase development modes. "
        "Supabase mode supports email auth, Google web/native iOS PKCE, session refresh, "
        "group text chat with read receipts, request activity, public profiles, "
        "order history, scoped search, account preferences, private reports, "
        "saved post reminders, private verified post images, inbox archive/mute, "
        "scoped demo cleanup "
        "and database clock synchronization. "
        "Stealth intake supports optional local-only structured model extraction and follow-up; "
        "matches include free deterministic cost/role drafts, editable final terms "
        "and atomic formation after unanimous versioned consent. "
        "Requires ENVIRONMENT=local, loopback by default; explicit device tests require direct "
        "TLS, a private bind address and an exact client IP allowlist. Not a production adapter."
    )
    schema["servers"] = [{"url": "http://127.0.0.1:8000"}]
    return schema


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "docs" / "core.openapi.json"
    value = build_core_contract()
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(value['paths'])} core paths, no network calls.")
