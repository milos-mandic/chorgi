#!/bin/bash
# One-time setup: install bot as launchd agent + cloudflared as system daemon
# Usage: bash bin/setup_launchd.sh

set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_SRC="$REPO/launchd/com.chorgi.bot.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.chorgi.bot.plist"
SECRETS="$REPO/.personal/secrets.env"

echo "=== Chorgi Bot launchd Setup ==="
echo ""

# --- Sanity checks ---
if [ ! -f "$PLIST_SRC" ]; then
    echo "ERROR: plist not found at $PLIST_SRC"
    exit 1
fi

if [ ! -f "$REPO/.venv/bin/python3" ]; then
    echo "ERROR: venv not found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

if [ ! -f "$SECRETS" ]; then
    echo "ERROR: .personal/secrets.env not found. Run: python setup.py"
    exit 1
fi

# --- Install bot plist ---
echo "Installing bot plist to ~/Library/LaunchAgents/ ..."
cp "$PLIST_SRC" "$PLIST_DEST"

# Unload existing if loaded (ignore errors — might not be loaded yet)
launchctl unload "$PLIST_DEST" 2>/dev/null || true

launchctl load "$PLIST_DEST"
echo "Bot service loaded."

# --- Install cloudflared ---
CLOUDFLARED_TOKEN=$(grep -E '^CLOUDFLARED_TOKEN=' "$SECRETS" 2>/dev/null | cut -d= -f2-)

if [ -z "$CLOUDFLARED_TOKEN" ]; then
    echo ""
    echo "WARNING: CLOUDFLARED_TOKEN not found in .personal/secrets.env"
    echo "Skipping cloudflared install. Add CLOUDFLARED_TOKEN to secrets.env and re-run."
else
    if ! command -v cloudflared &>/dev/null; then
        echo ""
        echo "WARNING: cloudflared not found in PATH."
        echo "Install with: brew install cloudflared"
        echo "Then re-run this script to set up the service."
    else
        echo ""
        echo "Installing cloudflared as system daemon ..."
        echo "(This requires sudo for /Library/LaunchDaemons/)"
        sudo cloudflared service install --token "$CLOUDFLARED_TOKEN"
        echo "cloudflared service installed."
    fi
fi

# --- Status ---
echo ""
echo "=== Status ==="
echo -n "Bot (com.chorgi.bot): "
launchctl list com.chorgi.bot 2>/dev/null | grep -E '"PID"|"LastExitStatus"' | tr '\n' ' ' || echo "not loaded"
echo ""

echo ""
echo "Done. Use 'bin/bot status' to check, 'bin/bot tail' to follow logs."
