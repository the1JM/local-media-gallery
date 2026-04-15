#!/bin/zsh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
SUPPORT_DIR="$HOME/Library/Application Support/Media Gallery"
PID_FILE="/tmp/screenshot-gallery.pid"
LOG_FILE="/tmp/screenshot-gallery.log"
SYNC_LOG_FILE="/tmp/screenshot-gallery-sync.log"
URL="http://127.0.0.1:8765"
SYNC_SCRIPT="$SUPPORT_DIR/sync_gallery_data.py"

is_server_up() {
  curl -s "$URL/api/library" >/dev/null 2>&1
}

cd "$PROJECT_DIR"
if ! is_server_up; then
  if [[ -f "$PID_FILE" ]]; then
    OLD_PID="$(cat "$PID_FILE")"
    if kill -0 "$OLD_PID" 2>/dev/null; then
      kill "$OLD_PID" 2>/dev/null || true
      sleep 1
    fi
  fi

  PORT_PID="$(lsof -ti tcp:8765 2>/dev/null || true)"
  if [[ -n "$PORT_PID" ]]; then
    kill "$PORT_PID" 2>/dev/null || true
    sleep 1
  fi

  nohup /usr/bin/python3 gallery_server.py >"$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"

  for _ in {1..20}; do
    if is_server_up; then
      break
    fi
    sleep 0.2
  done
fi

if [[ -f "$SYNC_SCRIPT" ]]; then
  nohup /usr/bin/python3 "$SYNC_SCRIPT" >"$SYNC_LOG_FILE" 2>&1 &
else
  nohup /usr/bin/python3 scripts/sync_gallery_data.py >"$SYNC_LOG_FILE" 2>&1 &
fi

open "$URL"
