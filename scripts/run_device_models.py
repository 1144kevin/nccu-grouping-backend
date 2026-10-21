"""Windows existing local models only. Check by default; no model downloads or cloud."""

import argparse
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def environment():
    return {
        **os.environ,
        "OLLAMA_HOST": "127.0.0.1:11434",
        "OLLAMA_MODELS": str(ROOT / ".local/ollama/models"),
        "OLLAMA_NO_CLOUD": "true",
        "OLLAMA_NOHISTORY": "true",
        "OLLAMA_DEBUG_LOG_REQUESTS": "false",
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_MAX_LOADED_MODELS": "1",
        "OLLAMA_MAX_QUEUE": "2",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    options = parser.parse_args()
    executable = ROOT / ".local/ollama/runtime/ollama.exe"
    if not executable.is_file() or not (ROOT / ".local/ollama/models").is_dir():
        raise SystemExit("Existing Windows Ollama/runtime/models required; nothing downloaded")
    print(
        "Existing models only; cloud disabled; loopback11434; one inference; bounded model queue."
    )
    if options.serve:
        raise SystemExit(subprocess.call([str(executable), "serve"], env=environment(), cwd=ROOT))
