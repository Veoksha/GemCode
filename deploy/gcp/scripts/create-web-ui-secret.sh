#!/usr/bin/env bash
# Create K8s secret for Web UI OAuth (run once before deploying web-ui).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
ENV_FILE="${ROOT_DIR}/gemcode-web-ui/.env.local"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — copy from .env.example and fill OAuth values."
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

: "${NEXTAUTH_URL:?}"
: "${NEXTAUTH_SECRET:?}"

if [[ "$NEXTAUTH_URL" =~ ^http://(localhost|127\.0\.0\.1) ]]; then
  echo "NOTE: NEXTAUTH_URL is local dev ($NEXTAUTH_URL)."
  echo "      For GKE production use https://your-domain.com (Google blocks http:// and raw IPs)."
elif [[ "$NEXTAUTH_URL" =~ ^http:// ]]; then
  echo "WARNING: NEXTAUTH_URL uses HTTP on a non-localhost host."
  echo "         Google OAuth will reject sign-in. Use HTTPS + a domain."
elif [[ "$NEXTAUTH_URL" =~ ^https://[0-9.]+ ]]; then
  echo "WARNING: NEXTAUTH_URL uses an IP address. Google OAuth requires a domain name."
fi
: "${GOOGLE_CLIENT_ID:?}"
: "${GOOGLE_CLIENT_SECRET:?}"

kubectl -n gemcode-platform create secret generic gemcode-web-ui-oauth \
  --from-literal=nextauth_url="$NEXTAUTH_URL" \
  --from-literal=nextauth_secret="$NEXTAUTH_SECRET" \
  --from-literal=google_client_id="$GOOGLE_CLIENT_ID" \
  --from-literal=google_client_secret="$GOOGLE_CLIENT_SECRET" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Secret gemcode-web-ui-oauth applied in gemcode-platform."
