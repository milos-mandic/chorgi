#!/bin/bash
# Chorgi_v1 watchdog — auto-restart on crash with rollback on crash loops
# Usage: nohup ~/projects/chorgi_v1/watchdog.sh &

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$HOME/.chorgi_v1_watchdog.log"

echo "$(date) Watchdog started" >> "$LOG"

# Ensure $PREFIX/tmp exists (Termux clears /tmp between sessions)
mkdir -p "${PREFIX:-/data/data/com.termux/files/usr}/tmp"

# Keep the device awake
command -v termux-wake-lock >/dev/null && termux-wake-lock

# Ensure services are running (called at startup and after each bot exit)
ensure_services() {
    if ! pgrep -f "cloudflared tunnel" > /dev/null; then
        echo "$(date) Starting cloudflared tunnel..." >> "$LOG"
        nohup cloudflared tunnel run chorgi >> "$HOME/cloudflared.log" 2>&1 &
    fi
}

ensure_services

# Crash loop detection: track last 3 exit timestamps
EXIT_TIMES=()
CRASH_WINDOW=60  # seconds
MAX_CRASHES=3

while true; do
    echo "$(date) Starting chorgi_v1 bot..." >> "$LOG"
    python3 "$SCRIPT_DIR/agent/main.py" 2>&1 | tee -a "$LOG"
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
