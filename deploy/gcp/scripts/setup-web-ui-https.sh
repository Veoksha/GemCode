#!/usr/bin/env bash
# Enable HTTPS on a custom domain for the in-cluster Web UI (Google OAuth requirement).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

: "${GEMCODE_WEB_DOMAIN:?Set GEMCODE_WEB_DOMAIN (e.g. app.gemcode.example.com)}"

if [[ "$GEMCODE_WEB_DOMAIN" =~ ^[0-9.]+$ ]]; then
  echo "ERROR: Google OAuth does not allow raw IP redirect URIs. Use a domain name."
  exit 1
fi

ORIGIN="https://${GEMCODE_WEB_DOMAIN}"
REDIRECT_URI="${ORIGIN}/api/auth/callback/google"

echo "==> Applying ManagedCertificate + Ingress for ${GEMCODE_WEB_DOMAIN}..."
sed -e "s|__GEMCODE_WEB_DOMAIN__|${GEMCODE_WEB_DOMAIN}|g" \
  deploy/gcp/k8s/platform/web-ui-ingress.yaml | kubectl apply -f -

echo ""
echo "==> DNS: point ${GEMCODE_WEB_DOMAIN} A record to the Web UI LoadBalancer IP:"
kubectl -n gemcode-platform get svc gemcode-web-ui -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true
echo ""
echo "==> After DNS propagates, wait for the managed cert (can take 15–60 min):"
echo "    kubectl -n gemcode-platform describe managedcertificate gemcode-web-ui-cert"
echo ""
echo "==> Google OAuth client (Web application) — add:"
echo "    Authorized JavaScript origins: ${ORIGIN}"
echo "    Authorized redirect URIs:       ${REDIRECT_URI}"
echo ""
echo "==> Update K8s secret and restart Web UI:"
echo "    NEXTAUTH_URL=${ORIGIN} ./deploy/gcp/scripts/create-web-ui-secret.sh"
echo "    kubectl -n gemcode-platform rollout restart deployment/gemcode-web-ui"
