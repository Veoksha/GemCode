"""
GemCode tenant provisioner — creates isolated GKE resources per user.

Run inside the cluster (recommended) or locally with kubeconfig.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from pydantic import BaseModel, Field

app = FastAPI(title="GemCode Tenant Provisioner", version="0.1.0")

TENANTS_NS = os.environ.get("TENANTS_NAMESPACE", "gemcode-tenants")
TEMPLATE_PATH = os.environ.get(
  "TENANT_MANIFEST_TEMPLATE",
  "/config/manifest.yaml.template",
)
TENANT_IMAGE = os.environ["TENANT_IMAGE"]
PVC_SIZE = os.environ.get("TENANT_PVC_SIZE", "30Gi")
CPU_REQUEST = os.environ.get("TENANT_CPU_REQUEST", "500m")
CPU_LIMIT = os.environ.get("TENANT_CPU_LIMIT", "2")
MEM_REQUEST = os.environ.get("TENANT_MEM_REQUEST", "1Gi")
MEM_LIMIT = os.environ.get("TENANT_MEM_LIMIT", "4Gi")
ADMIN_TOKEN = os.environ.get("PROVISIONER_ADMIN_TOKEN", "").strip()


class ProvisionRequest(BaseModel):
  email: str = Field(..., min_length=3, max_length=320)
  display_name: str | None = None


class ProvisionResponse(BaseModel):
  ok: bool
  tenant_id: str
  service_host: str
  service_url: str
  created: bool


def _load_k8s() -> None:
  try:
    config.load_incluster_config()
  except config.ConfigException:
    config.load_kube_config()


def tenant_id_from_email(email: str) -> str:
  normalized = email.strip().lower()
  digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
  return f"u-{digest}"


def _safe_name(tenant_id: str) -> str:
  if not re.fullmatch(r"u-[a-f0-9]{16}", tenant_id):
    raise ValueError("invalid tenant id")
  return tenant_id


def _render_manifest(tenant_id: str) -> str:
  with open(TEMPLATE_PATH, encoding="utf-8") as f:
    body = f.read()
  return (
    body.replace("__TENANT_ID__", tenant_id)
    .replace("__TENANT_IMAGE__", TENANT_IMAGE)
    .replace("__PVC_SIZE__", PVC_SIZE)
    .replace("__CPU_REQUEST__", CPU_REQUEST)
    .replace("__CPU_LIMIT__", CPU_LIMIT)
    .replace("__MEM_REQUEST__", MEM_REQUEST)
    .replace("__MEM_LIMIT__", MEM_LIMIT)
  )


def _deployment_ready(tenant_id: str) -> bool:
  apps = client.AppsV1Api()
  try:
    dep = apps.read_namespaced_deployment(
      name=f"gemcode-tenant-{tenant_id}",
      namespace=TENANTS_NS,
    )
    return (dep.status.ready_replicas or 0) >= 1
  except ApiException:
    return False


@app.on_event("startup")
def startup() -> None:
  _load_k8s()


@app.get("/health")
def health() -> dict[str, str]:
  return {"status": "ok", "service": "gemcode-provisioner"}


def _check_admin(authorization: str | None) -> None:
  if not ADMIN_TOKEN:
    return
  if not authorization or not authorization.startswith("Bearer "):
    raise HTTPException(status_code=401, detail="missing bearer token")
  token = authorization.removeprefix("Bearer ").strip()
  if token != ADMIN_TOKEN:
    raise HTTPException(status_code=403, detail="invalid token")


@app.post("/v1/tenants/provision", response_model=ProvisionResponse)
def provision_tenant(
  req: ProvisionRequest,
  authorization: str | None = Header(default=None),
) -> ProvisionResponse:
  _check_admin(authorization)
  tenant_id = tenant_id_from_email(req.email)
  _safe_name(tenant_id)
  host = f"gemcode-tenant-{tenant_id}.{TENANTS_NS}.svc.cluster.local"
  url = f"http://{host}:3001"

  apps = client.AppsV1Api()
  created = False
  try:
    apps.read_namespaced_deployment(
      name=f"gemcode-tenant-{tenant_id}",
      namespace=TENANTS_NS,
    )
  except ApiException as exc:
    if exc.status != 404:
      raise HTTPException(status_code=500, detail=exc.reason) from exc
    manifest = _render_manifest(tenant_id)
    yaml_mod = __import__("yaml")
    for doc in yaml_mod.safe_load_all(manifest):
      if not doc:
        continue
      kind = doc.get("kind")
      if kind == "PersistentVolumeClaim":
        client.CoreV1Api().create_namespaced_persistent_volume_claim(
          namespace=TENANTS_NS, body=doc
        )
      elif kind == "Deployment":
        client.AppsV1Api().create_namespaced_deployment(namespace=TENANTS_NS, body=doc)
      elif kind == "Service":
        client.CoreV1Api().create_namespaced_service(namespace=TENANTS_NS, body=doc)
      else:
        raise HTTPException(status_code=500, detail=f"unsupported kind {kind}")
    created = True

  return ProvisionResponse(
    ok=True,
    tenant_id=tenant_id,
    service_host=host,
    service_url=url,
    created=created,
  )


@app.get("/v1/tenants/{tenant_id}/status")
def tenant_status(
  tenant_id: str,
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _check_admin(authorization)
  _safe_name(tenant_id)
  return {
    "ok": True,
    "tenant_id": tenant_id,
    "ready": _deployment_ready(tenant_id),
    "service_url": f"http://gemcode-tenant-{tenant_id}.{TENANTS_NS}.svc.cluster.local:3001",
  }
