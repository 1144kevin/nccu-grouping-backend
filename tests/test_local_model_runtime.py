from unittest.mock import Mock

import httpx
import pytest

from app.stealth.ollama import LocalModelError, OllamaDraftProvider
from scripts import local_model_runtime as runtime


def test_ready_service_is_not_started_twice(monkeypatch):
    monkeypatch.setattr(runtime, "available", lambda: True)
    start = Mock(side_effect=AssertionError("must reuse existing service"))
    monkeypatch.setattr(runtime.subprocess, "Popen", start)
    assert runtime.ensure_local_model()
    start.assert_not_called()


def test_missing_runtime_does_not_download_or_start(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    monkeypatch.setattr(runtime, "available", lambda: False)
    assert not runtime.ensure_local_model()


def test_starts_only_existing_project_runtime_without_credentials(monkeypatch, tmp_path):
    executable = tmp_path / ".local/ollama/runtime/ollama.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    ready = iter((False, True))
    monkeypatch.setattr(runtime, "available", lambda: next(ready))
    monkeypatch.setattr(
        runtime.os,
        "environ",
        {"PATH": "synthetic-path", "STEALTH_GEMINI_API_KEY": "synthetic-secret"},
    )
    start = Mock(return_value=Mock())
    monkeypatch.setattr(runtime.subprocess, "Popen", start)
    assert runtime.ensure_local_model()
    args, kwargs = start.call_args
    assert args[0] == [runtime.sys.executable, str(tmp_path / "scripts/run_local_model.py")]
    assert kwargs["env"] == {"PATH": "synthetic-path"}
    assert kwargs["stdin"] == runtime.subprocess.DEVNULL


def test_unreachable_model_is_reported_as_offline_instead_of_a_timeout():
    def disconnected(request):
        raise httpx.ConnectError("synthetic connection refused", request=request)

    provider = OllamaDraftProvider(
        "synthetic-local:1b", local_only_confirmed=True, transport=httpx.MockTransport(disconnected)
    )
    with pytest.raises(LocalModelError, match="^local_model_unavailable$"):
        provider.draft("測試文字")
