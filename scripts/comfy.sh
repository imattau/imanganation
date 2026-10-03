#!/usr/bin/env bash
# Start / stop / restart the local ComfyUI service that imanganation talks to.
#   scripts/comfy.sh start|stop|restart|status|log
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMFY="$ROOT/vendor/ComfyUI"
PY="$COMFY/.venv/bin/python"
LOG="$ROOT/comfyui.log"
PIDFILE="$ROOT/.comfyui.pid"
PORT="${COMFY_PORT:-8188}"

is_up() { curl -s --max-time 3 "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1; }

start() {
  if is_up; then echo "ComfyUI already up on :${PORT}"; return 0; fi
  [ -x "$PY" ] || { echo "ComfyUI venv missing; run scripts/apply_comfyui_patches.sh after install"; exit 1; }
  ( cd "$COMFY"
    setsid nohup "$PY" main.py \
      --extra-model-paths-config "$ROOT/config/comfyui_extra_model_paths.yaml" \
      --listen 127.0.0.1 --port "$PORT" > "$LOG" 2>&1 < /dev/null &
    echo $! > "$PIDFILE"
  )
  for _ in $(seq 1 40); do
    sleep 2
    if is_up; then echo "ComfyUI up on :${PORT} (pid $(cat "$PIDFILE"))"; return 0; fi
  done
  echo "ComfyUI failed to start; last log lines:"; tail -20 "$LOG"; exit 1
}

stop() {
  if [ -f "$PIDFILE" ]; then kill "$(cat "$PIDFILE")" 2>/dev/null || true; rm -f "$PIDFILE"; fi
  pkill -f "vendor/ComfyUI/main.py" 2>/dev/null || true
  sleep 2
  is_up && echo "still up (another process owns :${PORT})" || echo "ComfyUI stopped"
}

case "${1:-status}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  is_up && echo "up on :${PORT}" || echo "down" ;;
  log)     tail -n "${2:-60}" "$LOG" ;;
  *)       echo "usage: $0 start|stop|restart|status|log [n]"; exit 2 ;;
esac
