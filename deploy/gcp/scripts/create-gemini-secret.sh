#!/usr/bin/env bash
# Create gemcode-gemini-api-key secret in gemcode-tenants from GOOGLE_API_KEY env or .env file.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

KEY="${GOOGLE_API_KEY:-}"
if [[ -z "$KEY" && -f .env ]]; then
  KEY="$(grep -E '^GOOGLE_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
fi

if [[ -z "$KEY" ]]; then
  echo "Set GOOGLE_API_KEY or add it to repo .env" >&2
  exit 1
fi

kubectl -n gemcode-tenants create secret generic gemcode-gemini-api-key \
  --from-literal=GOOGLE_API_KEY="$KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> Secret gemcode-gemini-api-key applied in gemcode-tenants"
