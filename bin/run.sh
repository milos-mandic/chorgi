#!/bin/bash
# Minimal wrapper — kept for users who prefer direct shell invocation.
# For production use, prefer launchd: bin/setup_launchd.sh

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$ROOT_DIR/.venv/bin/python3"
[ -x "$PYTHON" ] || PYTHON="python3"

exec "$PYTHON" "$ROOT_DIR/agent/main.py"
