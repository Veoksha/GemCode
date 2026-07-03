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

echo "==> Platform deployed."
