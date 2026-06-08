#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
  PYTHON_BIN="python"
else
  echo "WARNING: .venv not found. Using current Python. Run scripts/setup_macos.sh first if needed."
  if command -v python3.11 >/dev/null 2>&1; then
    PYTHON_BIN="python3.11"
  else
    PYTHON_BIN="python3"
  fi
fi

"$PYTHON_BIN" main.py trading-session-check
"$PYTHON_BIN" main.py kis-check
"$PYTHON_BIN" main.py live-sync-account --broker kis --network
"$PYTHON_BIN" main.py reconcile-live-account --broker kis --network
