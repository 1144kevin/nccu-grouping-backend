"""Authenticated, bounded Gemini TTS. Keys and transcripts are never logged."""

import base64
import hashlib
import threading
import time
from collections import OrderedDict

import httpx
from fastapi import HTTPException
from pydantic import Field, SecretStr, field_validator

from app.core.cloud_security import WindowQuota
from app.stealth.schemas import StrictModel

MODEL = "gemini-3.8-flash-lite-tts"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
_slots = threading.BoundedSemaphore(2)
_quota = WindowQuota()
_lock = threading.Lock()
_cache = OrderedDict()


class SpeechInput(StrictModel):
    text: str = Field(min_length=1, max_length=600)

    @field_validator("text")
    @classmethod
    def valid_text(cls, text):
        if not text.strip() or "\0" in text:
            raise ValueError("invalid speech text")
        text.encode("utf-8")
        return text.strip()


def synthesize(value: SpeechInput, key: SecretStr, actor: str, *, transport=None):
    if not key.get_secret_value().strip():
        raise HTTPException(503, "stealth_gemini_not_configured")
    cache_key = (actor, hashlib.sha256(value.text.encode()).hexdigest())
    with _lock:
        cached = _cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 300:
            return dict(cached[1])
    if not _slots.acquire(timeout=8):
        raise HTTPException(503, "stealth_gemini_busy")
    try:
        if not _quota.allow(("actor", actor), 4) or not _quota.allow("project", 8):
            raise HTTPException(429, "stealth_gemini_rate_limit")
        payload = {
            "model": MODEL,
            "store": False,
            "input": [
                {
                    "type": "user_input",
                    "content": [
                        {
                            "type": "text",
                            "text": value.text,
                            "annotations": [
                                {
                                    "type": "speech_metadata",
                                    "style": (
                                    "Speak natural conversational Taiwanese Mandarin, "
                                    "warm and relaxed, "
                                        "with natural pauses. Read the transcript exactly."
                                    ),
                                }
                            ],
                        }
                    ],
                }
            ],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": "Kore"}]},
        }
        try:
            with httpx.Client(timeout=30, transport=transport, follow_redirects=False) as client:
                with client.stream(
                    "POST",
                    ENDPOINT,
                    headers={"x-goog-api-key": key.get_secret_value()},
                    json=payload,
                ) as response:
                    if response.status_code == 429:
                        raise HTTPException(429, "stealth_gemini_quota")
                    if response.status_code != 200:
                        raise HTTPException(503, "stealth_gemini_unavailable")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 5 * 1024 * 1024:
                            raise ValueError("audio too large")
                    import json

                    data = json.loads(body)
            parts = [
                part
                for step in data.get("steps", [])
                if step.get("type") == "model_output"
                for part in step.get("content", [])
                if part.get("type") == "audio"
            ]
            part = parts[-1]
            audio = base64.b64decode(part["data"], validate=True)
            if (
                part.get("mime_type") != "audio/wav"
                or not audio.startswith(b"RIFF")
                or not 44 <= len(audio) <= 3 * 1024 * 1024
            ):
                raise ValueError("invalid audio")
            result = {"mime_type": "audio/wav", "audio_base64": part["data"]}
        except httpx.HTTPError:
            raise HTTPException(503, "stealth_gemini_unavailable") from None
        except (ValueError, KeyError, TypeError, IndexError):
            raise HTTPException(502, "stealth_gemini_invalid_output") from None
        with _lock:
            _cache[cache_key] = (time.monotonic(), result)
            while len(_cache) > 16:
                _cache.popitem(last=False)
        return dict(result)
    finally:
        _slots.release()
