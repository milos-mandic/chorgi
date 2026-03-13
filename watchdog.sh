#!/bin/bash
# Chorgi_v1 watchdog — auto-restart on crash with rollback on crash loops
# Usage: nohup ~/projects/chorgi_v1/watchdog.sh &

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$HOME/.chorgi_v1_watchdog.log"

echo "$(date) Watchdog started" >> "$LOG"

# Ensure services are running (called at startup and after each bot exit)
ensure_services() {
    if ! pgrep -f "cloudflared" > /dev/null; then
        echo "$(date) Starting cloudflared tunnel..." >> "$LOG"
        CLOUDFLARED_TOKEN=$(grep CLOUDFLARED_TOKEN "$SCRIPT_DIR/.personal/secrets.env" 2>/dev/null | cut -d= -f2-)
        nohup /opt/homebrew/Cellar/cloudflared/2026.3.0/bin/cloudflared tunnel run --token "$CLOUDFLARED_TOKEN" >> "$HOME/cloudflared.log" 2>&1 &
    fi
}

ensure_services

# Background loop to keep services alive even while bot is running
(while true; do
    sleep 60
    ensure_services
done) &
SERVICE_MONITOR_PID=$!

# Crash loop detection: track last 3 exit timestamps
EXIT_TIMES=()
CRASH_WINDOW=60  # seconds
MAX_CRASHES=3

while true; do
    echo "$(date) Starting chorgi_v1 bot..." >> "$LOG"
    # Use venv Python if available, else system python3
    PYTHON="$SCRIPT_DIR/.venv/bin/python3"
    [ -x "$PYTHON" ] || PYTHON="python3"
    "$PYTHON" "$SCRIPT_DIR/agent/main.py" 2>&1 | tee -a "$LOG"
    EXIT_CODE=$?
    NOW=$(date +%s)

    # Record this exit timestamp
    EXIT_TIMES+=("$NOW")

    # Keep only the last MAX_CRASHES timestamps
    while [ ${#EXIT_TIMES[@]} -gt $MAX_CRASHES ]; do
        EXIT_TIMES=("${EXIT_TIMES[@]:1}")
    done

    # Check for crash loop: MAX_CRASHES exits within CRASH_WINDOW seconds
    if [ ${#EXIT_TIMES[@]} -ge $MAX_CRASHES ]; then
        OLDEST=${EXIT_TIMES[0]}
        DIFF=$((NOW - OLDEST))
        if [ $DIFF -le $CRASH_WINDOW ]; then
            echo "$(date) CRASH LOOP DETECTED ($MAX_CRASHES exits in ${DIFF}s). Rolling back..." >> "$LOG"
            cd "$SCRIPT_DIR" && git checkout . && git clean -fd
            EXIT_TIMES=()  # Reset counter after rollback
            echo "$(date) Rollback complete. Waiting 10s before restart..." >> "$LOG"
            sleep 10
            continue
        fi
    fi

    echo "$(date) Bot exited with code $EXIT_CODE, restarting in 5s..." >> "$LOG"
    ensure_services
    sleep 5
done
