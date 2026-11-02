"""Authenticated, bounded Gemini TTS. Keys and transcripts are never logged."""

import base64
import hashlib
import io
import json
import threading
import time
import wave
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


def speech_payload(text: str):
    payload = {
        "model": MODEL,
        "store": False,
        "input": [
            {
                "type": "user_input",
                "content": [
                    {
                        "type": "text",
                        "text": text,
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
    return payload


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
        if not _quota.allow(("actor", actor), 8) or not _quota.allow("project", 14):
            raise HTTPException(429, "stealth_gemini_rate_limit")
        payload = speech_payload(value.text)
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


def stream_speech(value: SpeechInput, key: SecretStr, actor: str, *, transport=None):
    """Forward PCM chunks as they arrive, rather than waiting for a complete WAV."""

    def event(kind, **fields):
        return "data: " + json.dumps({"type": kind, **fields}) + "\n\n"

    yield event("ready")
    cache_key = (actor, hashlib.sha256(value.text.encode()).hexdigest())
    with _lock:
        cached = _cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < 300:
        with wave.open(io.BytesIO(base64.b64decode(cached[1]["audio_base64"])), "rb") as wav:
            if wav.getnchannels() == 1 and wav.getsampwidth() == 2:
                while chunk := wav.readframes(4800):
                    yield event(
                        "audio",
                        data=base64.b64encode(chunk).decode(),
                        sample_rate=wav.getframerate(),
                    )
                yield event("done")
                return
    if not key.get_secret_value().strip():
        yield event("error", code="stealth_gemini_not_configured")
        return
    if not _slots.acquire(timeout=8):
        yield event("error", code="stealth_gemini_busy")
        return
    try:
        if not _quota.allow(("actor", actor), 8) or not _quota.allow("project", 14):
            yield event("error", code="stealth_gemini_rate_limit")
            return
        payload = speech_payload(value.text)
        payload["stream"] = True
        payload["response_format"] = {
            "type": "audio",
            "mime_type": "audio/l16",
            "sample_rate": 24000,
        }
        pcm = bytearray()
        complete = False
        try:
            with httpx.Client(timeout=30, transport=transport, follow_redirects=False) as client:
                with client.stream(
                    "POST",
                    ENDPOINT,
                    headers={"x-goog-api-key": key.get_secret_value()},
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        yield event(
                            "error",
                            code="stealth_gemini_quota"
                            if response.status_code == 429
                            else "stealth_gemini_unavailable",
                        )
                        return
                    for line in response.iter_lines():
                        if len(line) > 512000:
                            raise ValueError("oversized event")
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            break
                        data = json.loads(raw)
                        if data.get("event_type") == "interaction.completed":
                            complete = True
                        elif data.get("event_type") in ("error", "interaction.failed"):
                            raise ValueError("incomplete speech")
                        elif (
                            data.get("event_type") == "step.delta"
                            and data.get("delta", {}).get("type") == "audio"
                        ):
                            delta = data["delta"]
                            audio = base64.b64decode(delta["data"], validate=True)
                            if (
                                len(audio) % 2
                                or delta.get("sample_rate", 24000) != 24000
                                or delta.get("channels", 1) != 1
                            ):
                                raise ValueError("unexpected PCM")
                            pcm.extend(audio)
                            if len(pcm) > 3 * 1024 * 1024:
                                raise ValueError("speech too large")
                            yield event("audio", data=delta["data"], sample_rate=24000)
            if not pcm or not complete:
                raise ValueError("incomplete speech")
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(pcm)
            result = {
                "mime_type": "audio/wav",
                "audio_base64": base64.b64encode(buffer.getvalue()).decode(),
            }
            with _lock:
                _cache[cache_key] = (time.monotonic(), result)
                while len(_cache) > 16:
                    _cache.popitem(last=False)
            yield event("done")
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            yield event("error", code="stealth_gemini_unavailable")
    finally:
        _slots.release()
