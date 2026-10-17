"""Download a pinned official portable runtime; no installer or OS changes.

Run once, then run_local_model.py in one terminal and its 'pull' in another.
Only this explicitly invoked setup writes local-ai.env; ordinary API requests
can never download models or change configuration.
"""

import hashlib
import zipfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/ollama/ollama/releases/download/v0.40.5/ollama-windows-amd64.zip"
DIGEST = "5134ce7e3bc8e94d0494721c799ec293b429450d0c52461b0ee3647dd3bb4bb7"


def main():
    directory = ROOT / ".local" / "ollama"
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / "runtime.zip"
    if not archive.exists():
        print("Downloading official Ollama v0.40.5 portable archive (~1.47 GB).", flush=True)
        with urlopen(URL, timeout=60) as response, archive.open("wb") as target:
            while chunk := response.read(1024 * 1024):
                target.write(chunk)
    with archive.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != DIGEST:
            raise SystemExit(
                "SHA256 mismatch; no files were extracted. Remove runtime.zip and retry."
            )
    destination = (directory / "runtime").resolve()
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            if not (destination / entry.filename).resolve().is_relative_to(destination):
                raise SystemExit("Invalid archive path; no files were extracted.")
        bundle.extractall(destination)
    config = ROOT / ".local" / "local-ai.env"
    if not config.exists():
        config.write_text(
            "STEALTH_AI_PROVIDER=ollama_local\nSTEALTH_OLLAMA_MODEL=qwen3.5:4b\n"
            "STEALTH_OLLAMA_LOCAL_ONLY_CONFIRMED=true\n",
            encoding="utf-8",
        )
    print("Verified and ready. Run scripts/run_local_model.py, then the same script with 'pull'.")


if __name__ == "__main__":
    main()
