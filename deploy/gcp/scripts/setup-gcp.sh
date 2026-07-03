#!/usr/bin/env bash
# Enable GCP APIs, Artifact Registry, Cloud NAT, and private GKE Autopilot cluster.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

if [[ -f deploy/gcp/config.env ]]; then
  # shellcheck disable=SC1091
  source deploy/gcp/config.env
else
  # shellcheck disable=SC1091
  source deploy/gcp/config.env.example
  echo "Using config.env.example — copy to deploy/gcp/config.env to customize."
fi

: "${GCP_PROJECT_ID:?GCP_PROJECT_ID required}"
: "${GCP_REGION:?GCP_REGION required}"
: "${GKE_CLUSTER_NAME:?GKE_CLUSTER_NAME required}"
: "${ARTIFACT_REGISTRY_REPO:?ARTIFACT_REGISTRY_REPO required}"

NAT_ROUTER="${NAT_ROUTER:-gemcode-nat-router}"
NAT_GATEWAY="${NAT_GATEWAY:-gemcode-nat}"

echo "==> Project: $GCP_PROJECT_ID  Region: $GCP_REGION"

gcloud config set project "$GCP_PROJECT_ID"

echo "==> Enabling APIs..."
gcloud services enable \
  container.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  iam.googleapis.com \
  compute.googleapis.com

echo "==> Artifact Registry repo..."
if ! gcloud artifacts repositories describe "$ARTIFACT_REGISTRY_REPO" \
  --location="$GCP_REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$ARTIFACT_REGISTRY_REPO" \
    --repository-format=docker \
    --location="$GCP_REGION" \
    --description="GemCode hosted tenant images"
fi

gcloud auth configure-docker "${GCP_REGION}-docker.pkg.dev" --quiet

echo "==> Cloud NAT (private nodes egress; satisfies vmExternalIpAccess org policy)..."
if ! gcloud compute routers describe "$NAT_ROUTER" --region="$GCP_REGION" >/dev/null 2>&1; then
  gcloud compute routers create "$NAT_ROUTER" \
    --region="$GCP_REGION" \
    --network=default
fi
if ! gcloud compute routers nats describe "$NAT_GATEWAY" \
  --router="$NAT_ROUTER" --region="$GCP_REGION" >/dev/null 2>&1; then
  gcloud compute routers nats create "$NAT_GATEWAY" \
    --router="$NAT_ROUTER" \
    --region="$GCP_REGION" \
    --nat-all-subnet-ip-ranges \
    --auto-allocate-nat-external-ips
fi

cluster_status=""
if gcloud container clusters describe "$GKE_CLUSTER_NAME" \
  --region="$GCP_REGION" >/dev/null 2>&1; then
  cluster_status="$(gcloud container clusters describe "$GKE_CLUSTER_NAME" \
    --region="$GCP_REGION" --format='value(status)')"
fi

if [[ "$cluster_status" == "ERROR" ]]; then
  echo "==> Deleting broken cluster $GKE_CLUSTER_NAME (status ERROR)..."
  gcloud container clusters delete "$GKE_CLUSTER_NAME" \
    --region="$GCP_REGION" --quiet --async
  echo "Waiting for delete..."
  while gcloud container clusters describe "$GKE_CLUSTER_NAME" \
    --region="$GCP_REGION" >/dev/null 2>&1; do sleep 15; done
  cluster_status=""
fi

echo "==> GKE Autopilot private cluster (takes ~5–10 min on first create)..."
if [[ -z "$cluster_status" ]]; then
  gcloud container clusters create-auto "$GKE_CLUSTER_NAME" \
    --region="$GCP_REGION" \
    --release-channel=regular \
    --network=default \
    --subnetwork=default \
    --enable-private-nodes \
    --master-ipv4-cidr=172.16.0.0/28
fi

gcloud container clusters get-credentials "$GKE_CLUSTER_NAME" --region="$GCP_REGION"

echo "==> Done. Next:"
echo "  1. GEMCODE_SOURCE=pypi ./deploy/gcp/scripts/build-images.sh"
echo "  2. ./deploy/gcp/scripts/deploy-platform.sh"
echo "  3. ./deploy/gcp/scripts/create-gemini-secret.sh"
echo "  4. ./deploy/gcp/scripts/create-tenant.sh user@example.com"
