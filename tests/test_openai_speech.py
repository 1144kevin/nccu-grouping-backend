import base64
import json

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.core_cloud.openai_budget import LocalBudget
from app.core_cloud.openai_speech import audio_chunks


class Socket:
    def __init__(self, events):
        self.events = iter(events)
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def send(self, value):
        self.sent.append(json.loads(value))

    def recv(self, timeout):
        return json.dumps(next(self.events))


def test_audio_stream_yields_valid_pcm_and_is_bounded(tmp_path):
    ws = Socket(
        [
            {
                "type": "response.output_audio.delta",
                "delta": base64.b64encode(b"\x00\x00").decode(),
            },
            {"type": "response.done", "response": {"status": "completed"}},
        ]
    )
    result = list(
        audio_chunks(
            "測試語音",
            SecretStr("synthetic"),
            connector=lambda *a, **k: ws,
            budget=LocalBudget(tmp_path / "budget.db"),
        )
    )
    assert result == [b"\x00\x00"]
    assert ws.sent[0]["session"]["audio"]["output"]["format"]["rate"] == 24000
    assert ws.sent[-1]["response"]["max_output_tokens"] == 1024


def test_incomplete_voice_is_not_success(tmp_path):
    ws = Socket([{"type": "response.done", "response": {"status": "failed"}}])
    with pytest.raises(HTTPException) as error:
        list(
            audio_chunks(
                "測試",
                SecretStr("synthetic"),
                connector=lambda *a, **k: ws,
                budget=LocalBudget(tmp_path / "budget.db"),
            )
        )
    assert error.value.detail == "openai_speech_unavailable"
