# GemCode multi-tenant hosting on GCP (GKE)

Isolated **one pod + one persistent disk per user**. Each pod runs `gemcode serve` with
`GEMCODE_HOSTED_TENANT_ROOT=/mnt/workspace` so tenants cannot access each other's files.

## Architecture

```
GKE Autopilot
├── gemcode-platform/     provisioner API
└── gemcode-tenants/      one Deployment+PVC+Service per user
         └── gemcode-tenant-u_<hash>  →  gemcode serve :3001
```

## Prerequisites

- `gcloud` CLI authenticated (`gcloud auth login`)
- `kubectl`, `docker`
- GCP project with billing enabled
- `GOOGLE_API_KEY` for Gemini (Secret Manager or `.env`)

## Quick start

```bash
# 1. Configure
cp deploy/gcp/config.env.example deploy/gcp/config.env
# edit GCP_PROJECT_ID if needed

# 2. Create cluster + Artifact Registry (~10 min first time)
chmod +x deploy/gcp/scripts/*.sh
./deploy/gcp/scripts/setup-gcp.sh

# 3. Build & push images (installs gemcode from repo by default)
./deploy/gcp/scripts/build-images.sh

# 4. Deploy platform (namespaces, network policy, provisioner)
./deploy/gcp/scripts/deploy-platform.sh

# 5. Gemini API key for tenant pods
./deploy/gcp/scripts/create-gemini-secret.sh

# 6. Create a tenant (one per user email)
./deploy/gcp/scripts/create-tenant.sh alice@example.com
./deploy/gcp/scripts/create-tenant.sh bob@example.com

# 7. Test API locally
kubectl -n gemcode-tenants port-forward svc/gemcode-tenant-u_<hash> 3001:3001
curl http://127.0.0.1:3001/api/health
```

Point **gemcode-web-ui** at the port-forwarded URL, or deploy a BFF that routes by user identity.

## Web UI (no tunnels for end users)

Deploy the Next.js UI **inside the cluster**. It talks to provisioner + gateway over
in-cluster DNS (`GEMCODE_IN_CLUSTER=true`) — users only open a public URL and sign in.

```bash
# Build & push web UI image
gcloud builds submit --config=deploy/gcp/cloudbuild-web-ui.yaml --project=$GCP_PROJECT_ID

# OAuth secret (from gemcode-web-ui/.env.local — use your public LoadBalancer URL as NEXTAUTH_URL)
chmod +x deploy/gcp/scripts/create-web-ui-secret.sh
./deploy/gcp/scripts/create-web-ui-secret.sh

# Deploy platform + web UI
DEPLOY_WEB_UI=1 ./deploy/gcp/scripts/deploy-platform.sh

# Google OAuth requires HTTPS + a domain (not http://<IP>). See deploy/gcp/AUTH.md
kubectl -n gemcode-platform get svc gemcode-web-ui
# GEMCODE_WEB_DOMAIN=app.yourdomain.com ./deploy/gcp/scripts/setup-web-ui-https.sh
```

### Local development (developers only)

One command — tunnels start automatically:

```bash
cd gemcode-web-ui && npm run dev:hosted
```

Do **not** ask end users to run `dev-tunnel.sh`; that is only for laptop dev.

## GemCode install source

| Build arg | Behavior |
|-----------|----------|
| `GEMCODE_SOURCE=repo` (default) | `pip install` from `./gemcode` — includes hosted tenant path locking |
| `GEMCODE_SOURCE=pypi` | `pip install gemcode==0.4.23` from PyPI |

```bash
GEMCODE_SOURCE=pypi ./deploy/gcp/scripts/build-images.sh
```

> PyPI **0.4.23+** includes hosted chat-store, habit chains/runs, and the HITL confirmation batch fix. Use `GEMCODE_SOURCE=pypi` after publishing, or `GEMCODE_SOURCE=repo` for Cloud Build from this tree.

## Provisioner API

Inside the cluster:

```bash
kubectl -n gemcode-platform port-forward svc/gemcode-provisioner 8080:8080

curl -X POST http://127.0.0.1:8080/v1/tenants/provision \
  -H 'Content-Type: application/json' \
  -d '{"email":"alice@example.com"}'
```

## Security notes

- NetworkPolicy blocks tenant pod ↔ tenant pod traffic
- Each tenant has a dedicated PVC (30Gi default)
- Client `path` / `project_root` cannot escape tenant root when hosted mode is on
- HITL approvals stored under `{workspace}/.gemcode/web_approvals`
- Add **IAP** or **Identity Platform** in front before production exposure

## Scaling beyond 22 users

- `create-tenant.sh` or provisioner API for each new user
- GKE Autopilot scales nodes automatically
- Tune `TENANT_CPU_*` / `TENANT_MEM_*` in `config.env`

## Files

| Path | Purpose |
|------|---------|
| `docker/gemcode-tenant/` | Tenant runtime image |
| `provisioner/` | HTTP API to create tenant K8s objects |
| `k8s/platform/` | Namespaces, RBAC, network policy |
| `k8s/tenant/manifest.yaml.template` | Per-tenant PVC+Deployment+Service |
| `scripts/` | setup, build, deploy, create-tenant |
