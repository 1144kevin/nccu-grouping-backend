import json

import httpx
import pytest
from pydantic import SecretStr

from app.core_cloud.openai_budget import LocalBudget
from app.core_cloud.openai_provider import OpenAIEmbeddingProvider, OpenAIExtractionProvider
from app.stealth.ollama import LocalModelError


def test_vision_text_and_schema_are_sent_to_openai_without_storage(tmp_path):
    def handler(request):
        assert request.url.host == "api.openai.com"
        data = json.loads(request.content)
        assert data["store"] is False
        assert data["input"][0]["content"][1]["type"] == "input_image"
        assert data["input"][0]["content"][1]["detail"] == "high"
        assert len([p for p in data["input"][0]["content"] if p["type"] == "input_image"]) == 2
        assert "schema" in data["instructions"]
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"text":"ok"}'}],
                    }
                ],
            },
        )

    provider = OpenAIExtractionProvider(
        SecretStr("synthetic-key"),
        transport=httpx.MockTransport(handler),
        budget=LocalBudget(tmp_path / "b.db"),
    )
    assert (
        provider.extract(
            [{"role": "user", "content": "describe", "images": ["YWJj", "ZGVm"]}],
            {"type": "object"},
            "prompt",
        )
        == '{"text":"ok"}'
    )


def test_embedding_is_512_normalized_and_versioned(tmp_path):
    def handler(request):
        payload = json.loads(request.content)
        assert payload["dimensions"] == 512
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 512}]})

    provider = OpenAIEmbeddingProvider(
        SecretStr("synthetic-key"),
        transport=httpx.MockTransport(handler),
        budget=LocalBudget(tmp_path / "b.db"),
    )
    value = provider.embed(["test"], purpose="query")
    assert value.model_id.startswith("openai:") and len(value.vectors[0]) == 512
    assert sum(x * x for x in value.vectors[0]) == pytest.approx(1)


def test_429_is_not_retried(tmp_path):
    seen = []

    def handler(request):
        seen.append(1)
        return httpx.Response(429)

    provider = OpenAIEmbeddingProvider(
        SecretStr("synthetic-key"),
        transport=httpx.MockTransport(handler),
        budget=LocalBudget(tmp_path / "b.db"),
    )
    with pytest.raises(LocalModelError, match="openai_quota_or_rate_limit"):
        provider.embed(["unique 429 test input"], purpose="query")
    assert len(seen) == 1


def test_oversized_combined_input_is_rejected_before_any_request(tmp_path):
    def handler(request):
        pytest.fail("oversized input must not call OpenAI")

    provider = OpenAIExtractionProvider(
        SecretStr("synthetic-key"),
        transport=httpx.MockTransport(handler),
        budget=LocalBudget(tmp_path / "b.db"),
    )
    with pytest.raises(LocalModelError, match="openai_input_too_large"):
        provider.extract([{"role": "user", "content": "x" * 7000}] * 5, {}, "prompt")
