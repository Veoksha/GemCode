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

: "${GCP_REGION:?}"
: "${GCP_PROJECT_ID:?}"
: "${ARTIFACT_REGISTRY_REPO:?}"
: "${GEMCODE_TENANT_IMAGE:?}"
: "${GEMCODE_PROVISIONER_IMAGE:?}"

GEMCODE_SOURCE="${GEMCODE_SOURCE:-repo}"
GEMCODE_PIP_VERSION="${GEMCODE_PIP_VERSION:-0.4.31}"

echo "==> Building tenant image (GEMCODE_SOURCE=$GEMCODE_SOURCE)..."
docker build \
  -f deploy/gcp/docker/gemcode-tenant/Dockerfile \
  --build-arg "GEMCODE_SOURCE=$GEMCODE_SOURCE" \
  --build-arg "GEMCODE_PIP_VERSION=$GEMCODE_PIP_VERSION" \
  -t "$GEMCODE_TENANT_IMAGE" \
  .

echo "==> Building provisioner image..."
docker build \
  -f deploy/gcp/provisioner/Dockerfile \
  -t "$GEMCODE_PROVISIONER_IMAGE" \
  .

echo "==> Pushing images..."
docker push "$GEMCODE_TENANT_IMAGE"
docker push "$GEMCODE_PROVISIONER_IMAGE"

echo "==> Images pushed:"
echo "  $GEMCODE_TENANT_IMAGE"
echo "  $GEMCODE_PROVISIONER_IMAGE"
