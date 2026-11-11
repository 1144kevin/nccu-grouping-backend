import json

import httpx
import pytest
from pydantic import SecretStr

from app.core_cloud.gemini_provider import GeminiEmbeddingProvider, GeminiExtractionProvider
from app.stealth.ollama import LocalModelError


def test_extract_sends_photo_and_schema_and_reads_structured_response():
    def handler(request):
        body = json.loads(request.content)
        assert request.url.host == "generativelanguage.googleapis.com"
        assert body["contents"][0]["parts"][1]["inlineData"]["data"] == "YWJj"
        assert body["generationConfig"]["responseJsonSchema"] == {"type": "object"}
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"finishReason": "STOP", "content": {"parts": [{"text": '{"text":"ok"}'}]}}
                ]
            },
        )

    provider = GeminiExtractionProvider(
        SecretStr("not-a-real-key"), transport=httpx.MockTransport(handler)
    )
    assert (
        provider.extract(
            [{"role": "user", "content": "test", "images": ["YWJj"]}],
            {"type": "object"},
            "instruction",
        )
        == '{"text":"ok"}'
    )


def test_embedding_uses_512_dimensions_normalizes_and_caches():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body["requests"][0]["taskType"] == "RETRIEVAL_DOCUMENT"
        assert body["requests"][0]["outputDimensionality"] == 512
        return httpx.Response(
            200, json={"embeddings": [{"values": [1.0] * 512} for _ in body["requests"]]}
        )

    provider = GeminiEmbeddingProvider(
        SecretStr("not-a-real-key"), transport=httpx.MockTransport(handler)
    )
    batch = provider.embed(["fixture unique embedding"], purpose="document")
    provider.embed(["fixture unique embedding"], purpose="document")
    assert len(calls) == 1 and len(batch.vectors[0]) == 512
    assert sum(x * x for x in batch.vectors[0]) == pytest.approx(1)


def test_quota_fails_closed_without_leaking_credentials():
    provider = GeminiExtractionProvider(
        SecretStr("not-a-real-key"),
        transport=httpx.MockTransport(lambda request: httpx.Response(429)),
    )
    with pytest.raises(LocalModelError, match="cloud_model_quota"):
        provider.extract([{"role": "user", "content": "x"}], {"type": "object"}, "instruction")
