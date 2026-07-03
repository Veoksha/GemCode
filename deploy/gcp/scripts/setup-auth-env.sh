#!/usr/bin/env bash
# Generate secrets and print Google OAuth setup steps for GemCode Web UI.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PROJECT="${GCP_PROJECT_ID:-finai-474319}"

SECRET="$(openssl rand -base64 32)"
PROV_TOKEN="$(openssl rand -hex 24)"

echo "==> GemCode auth setup (project: $PROJECT)"
echo ""
echo "1. Create OAuth Web client:"
echo "   https://console.cloud.google.com/apis/credentials?project=${PROJECT}"
echo "   Redirect URI: http://localhost:3002/api/auth/callback/google"
echo ""
echo "2. Add to gemcode-web-ui/.env.local:"
echo ""
cat <<EOF
NEXTAUTH_URL=http://localhost:3002
NEXTAUTH_SECRET=${SECRET}
GOOGLE_CLIENT_ID=<paste from console>
GOOGLE_CLIENT_SECRET=<paste from console>
GEMCODE_PROVISIONER_URL=http://127.0.0.1:8080
GEMCODE_PROVISIONER_TOKEN=${PROV_TOKEN}
GEMCODE_TENANT_GATEWAY_URL=http://127.0.0.1:3020
NEXT_PUBLIC_USE_REMOTE_FILES=true
EOF
echo ""
echo "3. Provisioner admin secret (optional but recommended):"
echo "   kubectl -n gemcode-platform create secret generic gemcode-provisioner-admin \\"
echo "     --from-literal=token=${PROV_TOKEN} --dry-run=client -o yaml | kubectl apply -f -"
echo ""
echo "4. Port-forwards (separate terminals):"
echo "   kubectl -n gemcode-platform port-forward svc/gemcode-provisioner 8080:8080"
echo "   kubectl -n gemcode-platform port-forward svc/gemcode-tenant-gateway 3020:8080"
echo ""
echo "5. Deploy gateway if not yet running:"
echo "   ./deploy/gcp/scripts/deploy-platform.sh"
echo ""
echo "See deploy/gcp/AUTH.md for full details."
