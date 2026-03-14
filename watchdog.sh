#!/bin/bash
# Minimal wrapper — kept for users who prefer direct shell invocation.
# For production use, prefer launchd: bin/setup_launchd.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"
[ -x "$PYTHON" ] || PYTHON="python3"

exec "$PYTHON" "$SCRIPT_DIR/agent/main.py"
