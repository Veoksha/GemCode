#!/usr/bin/env bash
# Provision one tenant: PVC + Deployment + Service (isolated gemcode serve pod).
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <email> [tenant_id_override]" >&2
  exit 1
fi

EMAIL="$1"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

if [[ -f deploy/gcp/config.env ]]; then
  # shellcheck disable=SC1091
  source deploy/gcp/config.env
else
  # shellcheck disable=SC1091
  source deploy/gcp/config.env.example
fi

: "${GEMCODE_TENANT_IMAGE:?}"
TENANT_PVC_SIZE="${TENANT_PVC_SIZE:-30Gi}"
TENANT_CPU_REQUEST="${TENANT_CPU_REQUEST:-500m}"
TENANT_CPU_LIMIT="${TENANT_CPU_LIMIT:-2}"
TENANT_MEM_REQUEST="${TENANT_MEM_REQUEST:-1Gi}"
TENANT_MEM_LIMIT="${TENANT_MEM_LIMIT:-4Gi}"

if [[ $# -ge 2 ]]; then
  TENANT_ID="$2"
else
  TENANT_ID="u-$(printf '%s' "$EMAIL" | tr '[:upper:]' '[:lower:]' | sha256sum | cut -c1-16)"
fi

if [[ ! "$TENANT_ID" =~ ^u-[a-f0-9]{16}$ ]]; then
  echo "Invalid tenant id: $TENANT_ID" >&2
  exit 1
fi

echo "==> Provisioning tenant $TENANT_ID for $EMAIL"

sed \
  -e "s|__TENANT_ID__|${TENANT_ID}|g" \
  -e "s|__TENANT_IMAGE__|${GEMCODE_TENANT_IMAGE}|g" \
  -e "s|__PVC_SIZE__|${TENANT_PVC_SIZE}|g" \
  -e "s|__CPU_REQUEST__|${TENANT_CPU_REQUEST}|g" \
  -e "s|__CPU_LIMIT__|${TENANT_CPU_LIMIT}|g" \
  -e "s|__MEM_REQUEST__|${TENANT_MEM_REQUEST}|g" \
  -e "s|__MEM_LIMIT__|${TENANT_MEM_LIMIT}|g" \
  deploy/gcp/k8s/tenant/manifest.yaml.template | kubectl apply -f -

echo "==> Waiting for pod..."
kubectl -n gemcode-tenants rollout status "deployment/gemcode-tenant-${TENANT_ID}" --timeout=300s

HOST="gemcode-tenant-${TENANT_ID}.gemcode-tenants.svc.cluster.local"
echo ""
echo "Tenant ready."
echo "  tenant_id:   $TENANT_ID"
echo "  email:       $EMAIL"
echo "  service:     http://${HOST}:3001"
echo ""
echo "Port-forward for local UI testing:"
echo "  kubectl -n gemcode-tenants port-forward svc/gemcode-tenant-${TENANT_ID} 3001:3001"
