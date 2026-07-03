# Hosted multi-tenant deployment

GemCode can run **one isolated `gemcode serve` per user** on shared infrastructure (GKE, VMs). Each process is locked to a single workspace so tenants cannot read or write each other's files via the web API.

## When to use hosted mode

| Scenario | Mode |
|----------|------|
| Local dev, single user | Default — no `GEMCODE_HOSTED_TENANT_ROOT` |
| Shared server, 22+ users, strict isolation | **Hosted** — one pod/process + PVC per user |

## Environment variables

Set on each tenant's `gemcode serve` process:

| Variable | Purpose |
|----------|---------|
| `GEMCODE_HOSTED_TENANT_ROOT` | Absolute path to the tenant workspace. When set, web handlers ignore or validate client `path` / `project_root` — requests outside this directory return **HTTP 403**. |
| `GEMCODE_WEB_PROJECT_ROOT` | Usually the same as `GEMCODE_HOSTED_TENANT_ROOT` (set automatically by `gemcode serve -C`). |

HITL approval files are stored under:

```text
{GEMCODE_HOSTED_TENANT_ROOT}/.gemcode/web_approvals/
```

instead of `~/.gemcode/web_approvals`.

## Health check

`GET /api/health` includes when hosted mode is active:

```json
{
  "status": "ok",
  "service": "gemcode-serve",
  "hosted_mode": true,
  "hosted_tenant_root": "/mnt/workspace",
  "project_root": "/mnt/workspace"
}
```

## REPL slash command

From an interactive session:

```text
/hosted          Show whether hosted tenant lock is active
/hosted status   Same
/hosted help     Env vars and doc pointer
```

## GCP reference implementation

The repo ships a full GKE layout under [`deploy/gcp/`](../deploy/gcp/):

- **One pod + persistent disk per user** in namespace `gemcode-tenants`
- **Network policy** — tenant pods cannot talk to each other
- **Provisioner API** — create tenant on first login (by email → `u_<hash>` id)
- **Docker image** — `pip install gemcode==0.4.21` (or build from repo)

Quick start:

```bash
cp deploy/gcp/config.env.example deploy/gcp/config.env
./deploy/gcp/scripts/setup-gcp.sh
./deploy/gcp/scripts/build-images.sh
./deploy/gcp/scripts/deploy-platform.sh
./deploy/gcp/scripts/create-gemini-secret.sh
./deploy/gcp/scripts/create-tenant.sh user@example.com
```

See [`deploy/gcp/README.md`](../deploy/gcp/README.md) and [`deploy/gcp/TROUBLESHOOTING.md`](../deploy/gcp/TROUBLESHOOTING.md).

## Security requirements (production)

Hosted mode **locks the API** to one directory per process. You still need:

1. **Authentication** (IAP, Identity Platform, Firebase) in front of the UI and API
2. **Routing** — map logged-in user → their tenant service (never expose raw pod URLs)
3. **Do not trust client paths** — BFF/gateway should set workspace server-side
4. **Resource quotas** — CPU, memory, disk per tenant (bash tools can be heavy)

## Related docs

- [`web-ui-contract.md`](web-ui-contract.md) — HTTP/SSE API
- [`configuration.md`](configuration.md) — all web env vars
- [`integrations.md`](integrations.md) — connecting a web UI
- [`operations.md`](operations.md) — PyPI release process
