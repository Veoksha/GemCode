#!/usr/bin/env bash
set -euo pipefail

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
: "${GEMCODE_PROVISIONER_IMAGE:?}"
: "${GEMCODE_GATEWAY_IMAGE:?}"

echo "==> Applying platform namespaces + network policy..."
kubectl apply -f deploy/gcp/k8s/platform/namespace.yaml
kubectl apply -f deploy/gcp/k8s/platform/network-policy.yaml
kubectl apply -f deploy/gcp/k8s/platform/provisioner-rbac.yaml

echo "==> Tenant manifest ConfigMap..."
kubectl -n gemcode-platform create configmap gemcode-tenant-manifest-template \
  --from-file=manifest.yaml.template=deploy/gcp/k8s/tenant/manifest.yaml.template \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> Provisioner deployment..."
sed \
  -e "s|__PROVISIONER_IMAGE__|${GEMCODE_PROVISIONER_IMAGE}|g" \
  -e "s|__TENANT_IMAGE__|${GEMCODE_TENANT_IMAGE}|g" \
  deploy/gcp/k8s/platform/provisioner-deployment.yaml | kubectl apply -f -

echo "==> Waiting for provisioner..."
kubectl -n gemcode-platform rollout status deployment/gemcode-provisioner --timeout=180s

echo "==> Tenant gateway..."
sed \
  -e "s|__GATEWAY_IMAGE__|${GEMCODE_GATEWAY_IMAGE}|g" \
  deploy/gcp/k8s/platform/gateway-deployment.yaml | kubectl apply -f -

echo "==> Waiting for tenant gateway..."
kubectl -n gemcode-platform rollout status deployment/gemcode-tenant-gateway --timeout=180s

if [[ "${DEPLOY_WEB_UI:-0}" == "1" ]]; then
  : "${GEMCODE_WEB_UI_IMAGE:?Set GEMCODE_WEB_UI_IMAGE or build web-ui image first}"
  echo "==> Web UI (in-cluster — no dev tunnels for users)..."
  sed \
    -e "s|__WEB_UI_IMAGE__|${GEMCODE_WEB_UI_IMAGE}|g" \
    deploy/gcp/k8s/platform/web-ui-deployment.yaml | kubectl apply -f -
  echo "==> Waiting for web UI..."
  kubectl -n gemcode-platform rollout status deployment/gemcode-web-ui --timeout=300s
  echo "==> Web UI external IP (add to Google OAuth redirect URIs):"
  kubectl -n gemcode-platform get svc gemcode-web-ui -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true
  echo ""
fi

echo "==> Platform deployed."
