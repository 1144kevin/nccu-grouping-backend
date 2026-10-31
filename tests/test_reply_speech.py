import base64
import json

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.core_cloud import reply_speech as module


def test_speech_is_bounded_private_and_cached_per_actor():
    module._cache.clear()
    module._quota.entries.clear()
    calls = []
    audio = base64.b64encode(b"RIFF" + b"\0" * 50).decode()

    def handle(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["store"] is False
        assert "fake-key" not in request.content.decode()
        assert "fake-key" not in str(request.url)
        return httpx.Response(
            200,
            json={
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "audio", "mime_type": "audio/wav", "data": audio}],
                    }
                ]
            },
        )

    value = module.SpeechInput(text="今天想喝什麼？")
    transport = httpx.MockTransport(handle)
    for actor in ("first", "first", "second"):
        result = module.synthesize(value, SecretStr("fake-key"), actor, transport=transport)
        assert result["audio_base64"] == audio
    assert len(calls) == 2


def test_speech_quota_errors_are_sanitized_without_retry():
    module._quota.entries.clear()

    def handle(request):
        return httpx.Response(429, text="private quota details")

    with pytest.raises(HTTPException) as caught:
        module.synthesize(
            module.SpeechInput(text="全新測試"),
            SecretStr("fake-key"),
            "quota",
            transport=httpx.MockTransport(handle),
        )
    assert caught.value.detail == "stealth_gemini_quota"
