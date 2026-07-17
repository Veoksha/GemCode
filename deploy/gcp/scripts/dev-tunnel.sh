#!/usr/bin/env bash
# Supervised GKE dev tunnels — auto-restart port-forwards when they drop.
#
# kubectl port-forward is NOT a stable production path. Streams die after idle
# timeouts, laptop sleep, or API-server blips (~15–20 min). This script keeps
# tunnels alive and restarts them within seconds.
#
# Usage:
#   ./deploy/gcp/scripts/dev-tunnel.sh              # gateway + provisioner (auth mode)
#   ./deploy/gcp/scripts/dev-tunnel.sh legacy       # single tenant on :3010
#   ./deploy/gcp/scripts/dev-tunnel.sh all          # everything
#
# Run in tmux/screen or background:
#   nohup ./deploy/gcp/scripts/dev-tunnel.sh >> /tmp/gemcode-tunnel.log 2>&1 &

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

if [[ -f deploy/gcp/config.env ]]; then
  # shellcheck disable=SC1091
  source deploy/gcp/config.env
fi

export PATH="/opt/homebrew/share/google-cloud-sdk/bin:${PATH:-}"
export USE_GKE_GCLOUD_AUTH_PLUGIN="${USE_GKE_GCLOUD_AUTH_PLUGIN:-True}"

MODE="${1:-gateway}"
TENANT_ID="${TENANT_ID:-u-ea0f89233f8c8f7b}"
PROACTIVE_RESTART_SEC="${PROACTIVE_RESTART_SEC:-900}"   # refresh stream before ~20m drops
HEALTH_INTERVAL_SEC="${HEALTH_INTERVAL_SEC:-20}"
RESTART_DELAY_SEC="${RESTART_DELAY_SEC:-2}"
WARMUP_SEC="${WARMUP_SEC:-5}"

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

require_kubectl() {
  if ! kubectl cluster-info >/dev/null 2>&1; then
    log "ERROR: kubectl cannot reach cluster. Check gcloud auth and USE_GKE_GCLOUD_AUTH_PLUGIN."
    exit 1
  fi
}

# name ns service local_port remote_port health_url
keep_forward() {
  local name="$1" ns="$2" svc="$3" lport="$4" rport="$5" health_url="${6:-}"

  while true; do
    log "▶ $name  127.0.0.1:${lport} → ${ns}/${svc}:${rport}"
    kubectl -n "$ns" port-forward "svc/${svc}" "${lport}:${rport}" &
    local pid=$!
    local started
    started=$(date +%s)
    sleep "$WARMUP_SEC"

    while kill -0 "$pid" 2>/dev/null; do
      local now elapsed
      now=$(date +%s)
      elapsed=$((now - started))

      if (( elapsed >= PROACTIVE_RESTART_SEC )); then
        log "↻ $name proactive refresh (${PROACTIVE_RESTART_SEC}s)"
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
        break
      fi

      if [[ -n "$health_url" ]]; then
        if ! curl -sf --max-time 4 "$health_url" >/dev/null 2>&1; then
          log "✗ $name health failed ($health_url) — restarting tunnel"
          kill "$pid" 2>/dev/null || true
          wait "$pid" 2>/dev/null || true
          break
        fi
      fi

      sleep "$HEALTH_INTERVAL_SEC"
    done

    if kill -0 "$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null || true
    fi
    log "■ $name tunnel ended — retry in ${RESTART_DELAY_SEC}s"
    sleep "$RESTART_DELAY_SEC"
  done
}

require_kubectl

log "GemCode dev tunnel supervisor (mode=$MODE)"
log "Tenant pods stay running on GKE — only this local tunnel restarts."
log "Press Ctrl+C to stop."

pids=()
cleanup() {
  log "Stopping tunnels…"
  for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done
  exit 0
}
trap cleanup INT TERM

start() {
  keep_forward "$@" &
  pids+=($!)
}

case "$MODE" in
  legacy)
    start "tenant" gemcode-tenants "gemcode-tenant-${TENANT_ID}" 3010 3001 \
      "http://127.0.0.1:3010/api/health"
    ;;
  gateway)
    start "provisioner" gemcode-platform gemcode-provisioner 8080 8080 \
      "http://127.0.0.1:8080/health"
    start "gateway" gemcode-platform gemcode-tenant-gateway 3020 8080 \
      "http://127.0.0.1:3020/health"
    ;;
  all)
    start "provisioner" gemcode-platform gemcode-provisioner 8080 8080 \
      "http://127.0.0.1:8080/health"
    start "gateway" gemcode-platform gemcode-tenant-gateway 3020 8080 \
      "http://127.0.0.1:3020/health"
    start "tenant" gemcode-tenants "gemcode-tenant-${TENANT_ID}" 3010 3001 \
      "http://127.0.0.1:3010/api/health"
    ;;
  *)
    echo "Unknown mode: $MODE (use legacy | gateway | all)"
    exit 1
    ;;
esac

wait
