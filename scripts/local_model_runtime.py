"""Start only the existing project-local runtime; never install/pull a model."""

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def available() -> bool:
    try:
        with httpx.Client(timeout=1, trust_env=False, follow_redirects=False) as client:
            response = client.get("http://127.0.0.1:11434/api/tags")
            return response.status_code == 200 and isinstance(response.json().get("models"), list)
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


def ensure_local_model() -> bool:
    if available():
        return True
    if not (ROOT / ".local/ollama/runtime/ollama.exe").is_file():
        return False
    # The model runtime has no use for API keys, sessions or passwords.
    environment = {
        name: value
        for name, value in os.environ.items()
        if not any(word in name.upper() for word in ("KEY", "TOKEN", "PASSWORD", "SECRET"))
    }
    try:
        with (
            (ROOT / ".local/model-runtime.out.log").open("ab") as output,
            (ROOT / ".local/model-runtime.err.log").open("ab") as errors,
        ):
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "scripts/run_local_model.py")],
                cwd=ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=errors,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if available():
                return True
            if process.poll() is not None:
                return False
            time.sleep(0.25)
    except OSError:
        return False
    return False
