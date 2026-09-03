#!/bin/sh
# Runtime self-healing supervisor.
# It never changes Railway variables or node identity. If the existing
# deployment becomes unhealthy, it terminates boot.sh and lets Railway's
# ON_FAILURE policy restart the same deployment with the same persisted
# UUID/REALITY keys/short IDs/subscription token.
set -eu

D="${RAILWAY_VOLUME_MOUNT_PATH:-${DATA_DIR:-/data}}"
mkdir -p "$D"
LOG="$D/supervisor.log"
STARTUP_GRACE="${SUPERVISOR_STARTUP_GRACE:-180}"
CHECK_INTERVAL="${SUPERVISOR_CHECK_INTERVAL:-15}"
FAILURES="${SUPERVISOR_FAILURES:-4}"

case "$STARTUP_GRACE" in ''|*[!0-9]*) STARTUP_GRACE=180;; esac
case "$CHECK_INTERVAL" in ''|*[!0-9]*) CHECK_INTERVAL=15;; esac
case "$FAILURES" in ''|*[!0-9]*) FAILURES=4;; esac
[ "$CHECK_INTERVAL" -ge 2 ] || CHECK_INTERVAL=2
[ "$FAILURES" -ge 2 ] || FAILURES=2

log() {
  printf '[supervisor] %s\n' "$*" | tee -a "$LOG"
}

cleanup() {
  if [ -n "${BOOT_PID:-}" ]; then
    kill -TERM "$BOOT_PID" 2>/dev/null || true
  fi
}
trap cleanup INT TERM EXIT

log "START startup_grace=${STARTUP_GRACE}s interval=${CHECK_INTERVAL}s failures=${FAILURES}"
/opt/xray/scripts/boot.sh >>"$LOG" 2>&1 &
BOOT_PID=$!

# Give the normal boot sequence enough time to discover Railway networking,
# generate runtime state and start all local listeners. During this period the
# supervisor only checks whether boot.sh is still alive.
elapsed=0
while [ "$elapsed" -lt "$STARTUP_GRACE" ]; do
  if ! kill -0 "$BOOT_PID" 2>/dev/null; then
    wait "$BOOT_PID" 2>/dev/null || true
    log "BOOT_EXITED_DURING_STARTUP"
    exit 1
  fi
  sleep "$CHECK_INTERVAL"
  elapsed=$((elapsed + CHECK_INTERVAL))
done

fail_count=0
while kill -0 "$BOOT_PID" 2>/dev/null; do
  # /ready validates the current runtime manifest, subscription endpoints,
  # every Xray listener, and Cloudflare Tunnel readiness when Node 5 is enabled.
  # It is intentionally read-only: the watchdog never regenerates node data.
  if python3 - <<'PY'
import sys
import urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=4) as r:
        raise SystemExit(0 if r.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
  then
    if [ "$fail_count" -gt 0 ]; then
      log "READY_RECOVERED previous_failures=$fail_count"
    fi
    fail_count=0
  else
    fail_count=$((fail_count + 1))
    log "READY_FAILURE count=${fail_count}/${FAILURES}"
    if [ "$fail_count" -ge "$FAILURES" ]; then
      log "FATAL READY_UNHEALTHY restarting_container=true"
      kill -TERM "$BOOT_PID" 2>/dev/null || true
      sleep 2
      kill -KILL "$BOOT_PID" 2>/dev/null || true
      wait "$BOOT_PID" 2>/dev/null || true
      exit 1
    fi
  fi
  sleep "$CHECK_INTERVAL"
done

wait "$BOOT_PID" 2>/dev/null || true
log "BOOT_EXITED restarting_container=true"
exit 1
