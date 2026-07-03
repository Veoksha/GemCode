# GCP troubleshooting

## GKE Autopilot: `constraints/compute.vmExternalIpAccess` violated

**Symptom:** Cluster create fails with:

```text
Constraint constraints/compute.vmExternalIpAccess violated for project ...
```

**Cause:** Your GCP organization blocks VMs from having external IPs. GKE node pools need an exception.

**Fix (org admin):**

1. Open **Organization policies** in Cloud Console
2. Find `compute.vmExternalIpAccess`
3. Either:
   - Add project `finai-474319` to the allowed list, or
   - Set policy to **Allow** for this project

Then delete the failed cluster and re-run setup:

```bash
gcloud container clusters delete gemcode-hosting --region=us-central1 --quiet
./deploy/gcp/scripts/setup-gcp.sh
```

**Alternative:** Deploy on a **private GKE** cluster with Cloud NAT (no external node IPs). This requires additional VPC/NAT setup — not included in the default scripts yet.

## Artifact Registry already created

If setup partially succeeded, Artifact Registry repo `gemcode` in `us-central1` should exist. Re-run `build-images.sh` after fixing the cluster.

## Verify tenant image locally (no GKE)

```bash
docker build -f deploy/gcp/docker/gemcode-tenant/Dockerfile -t gemcode-tenant:local .
docker run --rm -p 3001:3001 -e GOOGLE_API_KEY="$GOOGLE_API_KEY" gemcode-tenant:local
curl http://127.0.0.1:3001/api/health
```
