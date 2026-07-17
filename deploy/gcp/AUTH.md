# GemCode Web UI — Google Auth + multi-tenant GKE

## Overview

When auth is enabled, users sign in with **Google**. On first login the UI calls the **tenant provisioner**, which creates an isolated GKE pod + PVC. All API routes proxy to that user's tenant via the **tenant gateway**.

```
Browser → Next.js (auth) → Tenant Gateway /t/{tenant_id}/… → gemcode serve pod
                              ↑
                    Provisioner (first login)
```

## 1. Google OAuth credentials

1. Open [Google Cloud Console → APIs & Credentials](https://console.cloud.google.com/apis/credentials?project=finai-474319)
2. **OAuth consent screen** — configure (Internal for Workspace org, or External for testing)
3. **Create credentials → OAuth client ID → Web application**
4. **Authorized JavaScript origins:**
   ```
   http://localhost:3002
   ```
5. **Authorized redirect URIs:**
   ```
   http://localhost:3002/api/auth/callback/google
   ```
6. Copy **Client ID** and **Client secret**

### Production (GKE / Vercel) — HTTPS + domain required

Google **rejects** OAuth redirect URIs that use:

- `http://` on anything except `localhost` / `127.0.0.1`
- Raw IP addresses (e.g. `http://35.238.244.56`)

You will see **Error 400: invalid_request** — *"doesn't comply with Google's OAuth 2.0 policy"*.

**Fix:** serve the Web UI on a real domain with HTTPS, then register:

| Field | Example |
|-------|---------|
| Authorized JavaScript origins | `https://app.yourdomain.com` |
| Authorized redirect URIs | `https://app.yourdomain.com/api/auth/callback/google` |

Set `NEXTAUTH_URL` to the same origin (no trailing slash) in the K8s secret or Vercel env.

```bash
# GKE: after DNS A record points to the Web UI LoadBalancer IP
GEMCODE_WEB_DOMAIN=app.yourdomain.com ./deploy/gcp/scripts/setup-web-ui-https.sh
NEXTAUTH_URL=https://app.yourdomain.com ./deploy/gcp/scripts/create-web-ui-secret.sh
kubectl -n gemcode-platform rollout restart deployment/gemcode-web-ui
```

If the OAuth consent screen is **External → Testing**, add each user's Google email under **Test users**.

### `redirect_uri_mismatch` (Error 400)

NextAuth sends `http://localhost:3002/api/auth/callback/google`. If that exact URI is missing from the OAuth client, Google blocks sign-in.

```bash
./deploy/gcp/scripts/sync-oauth-redirect.sh
```

Opens the credentials page — add the origin and redirect URI above, save, then retry sign-in. `NEXTAUTH_URL` in `.env.local` must match the origin (no trailing slash).

Generate a session secret:

```bash
openssl rand -base64 32
```

## 2. Web UI `.env.local`

```env
# Auth (set AUTH_DISABLED=true to skip during local single-tenant dev)
NEXTAUTH_URL=http://localhost:3002
NEXTAUTH_SECRET=<openssl output>
GOOGLE_CLIENT_ID=<from console>
GOOGLE_CLIENT_SECRET=<from console>

# Tenant routing (local dev with kubectl port-forwards)
GEMCODE_PROVISIONER_URL=http://127.0.0.1:8080
GEMCODE_PROVISIONER_TOKEN=<optional bearer token>
GEMCODE_TENANT_GATEWAY_URL=http://127.0.0.1:3020

# Legacy single-tenant fallback when AUTH_DISABLED=true
# NEXT_PUBLIC_API_URL=http://127.0.0.1:3010
# AUTH_DISABLED=true
```

## 3. Port-forwards (local dev against GKE)

Run in separate terminals:

```bash
# Provisioner API
kubectl -n gemcode-platform port-forward svc/gemcode-provisioner 8080:8080

# Tenant gateway (routes all users after auth)
kubectl -n gemcode-platform port-forward svc/gemcode-tenant-gateway 3020:8080
```

## 4. Provisioner admin token (recommended)

```bash
TOKEN=$(openssl rand -hex 24)
kubectl -n gemcode-platform create secret generic gemcode-provisioner-admin \
  --from-literal=token="$TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -
# Set PROVISIONER_ADMIN_TOKEN on provisioner deployment + GEMCODE_PROVISIONER_TOKEN in .env.local
```

## 5. Deploy gateway to GKE

```bash
source deploy/gcp/config.env
gcloud builds submit --config=deploy/gcp/cloudbuild.yaml --project=finai-474319
./deploy/gcp/scripts/deploy-platform.sh
```

## 6. Flow

1. User opens http://localhost:3002 → redirected to `/login`
2. **Continue with Google** → NextAuth session
3. `POST /api/tenant/ensure` → provisioner creates `gemcode-tenant-u-{hash}` if new
4. Chat, files, terminal, skills → `/api/*` → gateway → user's pod

Each email maps to a deterministic tenant id (same as `create-tenant.sh`).
