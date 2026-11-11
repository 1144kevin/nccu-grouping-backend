"""Local-only OpenAI Realtime audio; no key, transcript or audio is logged."""

import base64
import io
import json
import time
import wave

from fastapi import HTTPException
from pydantic import SecretStr
from websockets.sync.client import connect

from app.core_cloud.openai_budget import DEFAULT_LEDGER, LocalBudget, check_rate

MODEL = "gpt-realtime-2.1-mini"


def audio_chunks(text: str, key: SecretStr, *, connector=connect, budget=None):
    if not key.get_secret_value().strip():
        raise HTTPException(503, "openai_not_configured")
    if not text.strip() or len(text) > 600:
        raise HTTPException(422, "invalid_speech_text")
    check_rate("speech")
    (budget or LocalBudget(DEFAULT_LEDGER)).reserve(100000, "speech")
    try:
        with connector(
            "wss://api.openai.com/v1/realtime?model=" + MODEL,
            additional_headers={"Authorization": "Bearer " + key.get_secret_value()},
            open_timeout=8,
            close_timeout=2,
            max_size=1_000_000,
            proxy=None,
        ) as ws:
            ws.send(
                json.dumps(
                    {
                        "type": "session.update",
                        "session": {
                            "type": "realtime",
                            "output_modalities": ["audio"],
                            "instructions": "你是繁體中文語音朗讀器。"
                            "只朗讀使用者提供的文字，不回答、不新增內容。",
                            "audio": {
                                "output": {
                                    "format": {"type": "audio/pcm", "rate": 24000},
                                    "voice": "marin",
                                }
                            },
                        },
                    }
                )
            )
            ws.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": text}],
                        },
                    }
                )
            )
            ws.send(
                json.dumps(
                    {
                        "type": "response.create",
                        "response": {"output_modalities": ["audio"], "max_output_tokens": 1024},
                    }
                )
            )
            start = time.monotonic()
            total = 0
            while time.monotonic() - start < 45:
                event = json.loads(ws.recv(timeout=10))
                if event.get("type") == "error":
                    raise ValueError("provider_error")
                if event.get("type") == "response.output_audio.delta":
                    chunk = base64.b64decode(event["delta"], validate=True)
                    total += len(chunk)
                    if total > 3 * 1024 * 1024:
                        raise ValueError("too_large")
                    yield chunk
                if event.get("type") == "response.done":
                    if event.get("response", {}).get("status") != "completed" or total == 0:
                        raise ValueError("incomplete")
                    return
            raise TimeoutError()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "openai_speech_unavailable") from None


def stream_speech(value, key, actor, *, budget=None):
    def event(kind, **fields):
        return "data: " + json.dumps({"type": kind, **fields}) + "\n\n"

    yield event("ready")
    try:
        for chunk in audio_chunks(value.text, key, budget=budget):
            yield event("audio", data=base64.b64encode(chunk).decode("ascii"), sample_rate=24000)
        yield event("done")
    except HTTPException as exc:
        yield event("error", code=exc.detail)


def synthesize(value, key, actor, *, budget=None):
    pcm = b"".join(audio_chunks(value.text, key, budget=budget))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(24000)
        writer.writeframes(pcm)
    return {
        "mime_type": "audio/wav",
        "audio_base64": base64.b64encode(buf.getvalue()).decode("ascii"),
    }
