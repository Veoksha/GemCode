#!/usr/bin/env bash
# One command for local hosted dev: GKE tunnels + Next.js Web UI.
#
# Usage (from repo root or gemcode-web-ui):
#   npm run dev:hosted          # in gemcode-web-ui/
#   ./deploy/gcp/scripts/dev-hosted.sh
#
# End users on the deployed cloud UI never run this — only developers on a laptop.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
UI_DIR="${ROOT_DIR}/gemcode-web-ui"
TUNNEL_SCRIPT="${ROOT_DIR}/deploy/gcp/scripts/dev-tunnel.sh"

export PATH="/opt/homebrew/share/google-cloud-sdk/bin:${PATH:-}"
export USE_GKE_GCLOUD_AUTH_PLUGIN="${USE_GKE_GCLOUD_AUTH_PLUGIN:-True}"

log() { printf '[dev-hosted] %s\n' "$*"; }

cleanup() {
  if [[ -n "${TUNNEL_PID:-}" ]] && kill -0 "$TUNNEL_PID" 2>/dev/null; then
    log "Stopping tunnel supervisor (pid $TUNNEL_PID)…"
    kill "$TUNNEL_PID" 2>/dev/null || true
    wait "$TUNNEL_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if [[ ! -d "$UI_DIR" ]]; then
  log "ERROR: gemcode-web-ui not found at $UI_DIR"
  exit 1
fi

log "Starting GKE tunnels (provisioner :8080, gateway :3020)…"
"$TUNNEL_SCRIPT" gateway &
TUNNEL_PID=$!

log "Waiting for tunnels (up to 60s)…"
ready=false
for _ in $(seq 1 30); do
  if curl -sf --max-time 3 http://127.0.0.1:8080/health >/dev/null \
    && curl -sf --max-time 3 http://127.0.0.1:3020/health >/dev/null; then
    ready=true
    break
  fi
  sleep 2
done

if [[ "$ready" != "true" ]]; then
  log "ERROR: Tunnels did not become ready. Check: kubectl cluster-info"
  log "  gcloud container clusters get-credentials gemcode-hosting --region us-central1"
  exit 1
fi

log "Tunnels ready. Starting Web UI on http://localhost:3002"
cd "$UI_DIR"
exec npm run dev
