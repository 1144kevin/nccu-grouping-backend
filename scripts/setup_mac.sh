#!/bin/bash
# Run with bash scripts/setup_mac.sh. Never copies Windows environments or private keys.
set -eu
if [ "$(uname -s)" != "Darwin" ]; then
  echo "This setup is for macOS. Nothing was installed."
  exit 1
fi
cd "$(dirname "$0")/.."
if ! command -v uv >/dev/null 2>&1; then
  echo "Install uv from https://docs.astral.sh/uv/getting-started/installation/ then retry."
  exit 1
fi
umask 077
uv sync --frozen
mkdir -p .local
if [ ! -f .local/supabase-dev.env ]; then
  cp config/supabase-dev.env.example .local/supabase-dev.env
fi
echo "Edit .local/supabase-dev.env: enter the development Publishable key."
echo "Then run: .venv/bin/python scripts/run_supabase_core.py --check"
echo "Start: .venv/bin/python scripts/run_supabase_core.py"
echo "No network listener, certificate installation, deployment or paid service was started."
.venv/bin/python scripts/mac_handoff.py check
