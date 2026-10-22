"""Verify the feed with synthetic public posts and installed loopback embeddings only."""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-local", action="store_true")
    if not parser.parse_args().run_local:
        print("Dry run. --run-local uses installed local models, no database or model downloads.")
        return
    from app.core_cloud.feed_semantic import semantic_feed
    from app.stealth.local_providers import OllamaEmbeddingProvider

    now = datetime.now(UTC)
    posts = []
    for text in [
        "想找同學一起喝冰紅茶飲料",
        "活動結束剩餘便當，免費分享給還沒吃飯的同學",
        "揪同學一起買筆記本與文具",
    ]:
        posts.append({
            "id": str(uuid4()), "author_id": str(uuid4()), "author_name": "合成測試",
            "content": text, "tags": ["分享"],
            "location": {"name": "政大正門", "latitude": 24.98, "longitude": 121.57},
            "price_per_person": None, "deadline_at": now + timedelta(hours=1),
            "capacity": 5, "joined": 1, "status": "open", "my_status": "none",
            "created_at": now, "version": 1,
            "recommendation": {
                "version": "rules_v2", "score": 12, "remaining_seats": 4, "as_of": now,
                "reasons": [{"code": "remaining_seats", "text": "尚有4個名額"}],
            },
        })
    service = SimpleNamespace(
        store=SimpleNamespace(
            token="synthetic-no-db", _request=lambda *a, **k: {"items": posts}
        ), preferences=lambda: SimpleNamespace(version=1),
    )
    result = semantic_feed(
        service, "想找晚餐",
        lambda: OllamaEmbeddingProvider("qwen3-embedding:0.6b", local_only_confirmed=True),
    )
    evidence = {
        "checked_at": datetime.now(UTC).isoformat(), "query": "想找晚餐",
        "status": result.status, "database": "none; synthetic session feed",
        "items": [
            {"content": p.content, "semantic": p.semantic_recommendation.model_dump()
             if p.semantic_recommendation else None} for p in result.items
        ],
    }
    out = ROOT / ".local" / "feed-semantic-smoke.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=True))
    assert result.status == "completed", "Local embeddings unavailable; no successful AI claim"
    assert "便當" in result.items[0].content, "Review the model ranking before claiming success"


if __name__ == "__main__":
    main()
