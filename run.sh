#!/bin/zsh
# coco one-click launcher: bootstrap on first run, start the server (if not already
# running) and open the browser. Safe to run repeatedly.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
PORT="${COCO_PORT:-8765}"
cd "$ROOT" || { echo "Cannot find project directory: $ROOT"; exit 1; }

# --- dependency checks ---------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install it (e.g. 'brew install python') and rerun."
  exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg not found. Install it with 'brew install ffmpeg' (needed for audio decoding)."
  exit 1
fi
if ! command -v claude >/dev/null 2>&1; then
  echo "Note: the 'claude' CLI was not found. Transcription works without it, but AI"
  echo "      analysis (reports, chat, brief, tracking) needs Claude Code installed and"
  echo "      logged in: https://docs.claude.com/claude-code"
fi

# --- first-run bootstrap: virtualenv + dependencies ----------------------------
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "First run: creating a virtual environment and installing dependencies…"
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install --upgrade pip >/dev/null
  "$ROOT/.venv/bin/pip" install -r "$ROOT/requirements.txt"
  echo "Setup done."
fi

# --- start the server if it is not already listening ---------------------------
if ! lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  mkdir -p "$ROOT/logs"
  PYTHONPATH="$ROOT" nohup "$ROOT/.venv/bin/python" -m coco web --port "$PORT" \
    >> "$ROOT/logs/coco.log" 2>&1 &
  echo "Starting coco server…"
  for i in {1..40}; do
    curl -s -o /dev/null "http://127.0.0.1:$PORT/" && break
    sleep 0.5
  done
fi

if ! lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "coco server failed to start. Check the log: $ROOT/logs/coco.log"
  exit 1
fi

open "http://127.0.0.1:$PORT"
echo "coco is ready → http://127.0.0.1:$PORT (log: logs/coco.log)"
