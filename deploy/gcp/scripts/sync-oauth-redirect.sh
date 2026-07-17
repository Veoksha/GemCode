#!/usr/bin/env bash
# Fix Google sign-in "Error 400: redirect_uri_mismatch" for GemCode Web UI.
# Standard OAuth Web clients must be edited in Cloud Console (no gcloud API).
set -euo pipefail
PROJECT="${GCP_PROJECT_ID:-finai-474319}"
ORIGIN="${GEMCODE_WEB_ORIGIN:-http://localhost:3002}"
REDIRECT_URI="${ORIGIN}/api/auth/callback/google"
ENV_FILE="$(cd "$(dirname "$0")/../../.." && pwd)/gemcode-web-ui/.env.local"
CLIENT_ID=""
if [[ -f "$ENV_FILE" ]]; then
  CLIENT_ID=$(grep '^GOOGLE_CLIENT_ID=' "$ENV_FILE" | cut -d= -f2- | tr -d '"')
fi

echo "Google OAuth — add these to your Web application client:"
echo ""
echo "  Authorized JavaScript origins:"
echo "    ${ORIGIN}"
echo ""
echo "  Authorized redirect URIs:"
echo "    ${REDIRECT_URI}"
echo ""
echo "Ensure gemcode-web-ui/.env.local has:"
echo "  NEXTAUTH_URL=${ORIGIN}"
echo ""

CONSOLE_URL="https://console.cloud.google.com/apis/credentials?project=${PROJECT}"
if [[ -n "$CLIENT_ID" ]]; then
  CONSOLE_URL="https://console.cloud.google.com/apis/credentials/oauthclient/${CLIENT_ID%%.*}?project=${PROJECT}"
fi
echo "Console: ${CONSOLE_URL}"
echo ""

if [[ "${OPEN_CONSOLE:-1}" == "1" ]] && command -v open >/dev/null 2>&1; then
  open "${CONSOLE_URL}" || true
fi
