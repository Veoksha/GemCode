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

## Port-forward keeps dying (`lost connection to pod`)

**Symptom:** `kubectl port-forward` works then drops after ~15–20 minutes with `lost connection to pod` or stream timeout errors.

**Cause:** This is **not** your tenant pod crashing (check with `kubectl get pods -n gemcode-tenants` — RESTARTS should stay 0). `kubectl port-forward` opens a fragile local stream through the Kubernetes API; it is meant for quick debugging, not all-day use.

**Fix (dev):** Use the supervised tunnel script — auto-restarts within seconds and proactively refreshes before typical drop time:

```bash
chmod +x deploy/gcp/scripts/dev-tunnel.sh

# Single-tenant (current .env.local with :3010)
./deploy/gcp/scripts/dev-tunnel.sh legacy

# Auth + multi-user (after gateway is deployed)
./deploy/gcp/scripts/dev-tunnel.sh gateway
```

Run in tmux or background:

```bash
nohup ./deploy/gcp/scripts/dev-tunnel.sh legacy >> /tmp/gemcode-tunnel.log 2>&1 &
```

**Your GKE pod stays running** — when the tunnel restarts, the workspace is instant (no cold start). The Web UI also re-checks health when you return to the browser tab.

**Fix (production):** Do not use port-forward. Deploy the tenant gateway + UI on GKE behind HTTPS (Ingress / Load Balancer). See `deploy/gcp/AUTH.md`.
