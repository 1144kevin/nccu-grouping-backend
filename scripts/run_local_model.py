"""Run the verified portable Ollama, scoped to this workspace and loopback."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".local" / "ollama" / "runtime" / "ollama.exe"


def main():
    if not RUNTIME.is_file():
        raise SystemExit("Portable Ollama missing; see docs/CORE_SUPABASE_DEV.md")
    action = sys.argv[1:]
    if action not in ([], ["pull"], ["pull-embedding"], ["status"]):
        raise SystemExit("Usage: run_local_model.py [pull|pull-embedding|status]")
    state = ROOT / ".local" / "ollama" / "home"
    state.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "USERPROFILE": str(state),
            "OLLAMA_HOST": "127.0.0.1:11434",
            "OLLAMA_MODELS": str(ROOT / ".local" / "ollama" / "models"),
            "OLLAMA_NO_CLOUD": "1",
            "OLLAMA_NUM_PARALLEL": "1",
            "OLLAMA_MAX_LOADED_MODELS": "1",
            "OLLAMA_MAX_QUEUE": "1",
            "OLLAMA_CONTEXT_LENGTH": "4096",
            "OLLAMA_DEBUG_LOG_REQUESTS": "false",
            "OLLAMA_NOHISTORY": "1",
        }
    )
    args = (
        ["pull", "qwen3-embedding:0.6b"]
        if action == ["pull-embedding"]
        else ["pull", "qwen3.5:4b"]
        if action == ["pull"]
        else ["ps"]
        if action
        else ["serve"]
    )
    print("Project-local Ollama; cloud disabled; loopback only. Ctrl+C stops it.", flush=True)
    try:
        subprocess.run([str(RUNTIME), *args], cwd=state, env=env, check=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
