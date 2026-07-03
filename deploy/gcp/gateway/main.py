"""Tenant HTTP gateway — routes /t/{tenant_id}/* to per-user gemcode serve pods."""

from __future__ import annotations

import os
import re

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

app = FastAPI(title="GemCode Tenant Gateway", version="0.1.0")

TENANTS_NS = os.environ.get("TENANTS_NAMESPACE", "gemcode-tenants")
TENANT_ID_RE = re.compile(r"^u-[a-f0-9]{16}$")


def _tenant_base(tenant_id: str) -> str:
  if not TENANT_ID_RE.fullmatch(tenant_id):
    raise HTTPException(status_code=400, detail="invalid tenant id")
  return f"http://gemcode-tenant-{tenant_id}.{TENANTS_NS}.svc.cluster.local:3001"


@app.get("/health")
def health() -> dict[str, str]:
  return {"status": "ok", "service": "gemcode-tenant-gateway"}


@app.api_route(
  "/t/{tenant_id}/{path:path}",
  methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy_tenant(tenant_id: str, path: str, request: Request) -> Response:
  base = _tenant_base(tenant_id)
  upstream = f"{base}/{path}"
  if request.url.query:
    upstream = f"{upstream}?{request.url.query}"

  headers = {
    k: v
    for k, v in request.headers.items()
    if k.lower() not in {"host", "content-length", "connection", "transfer-encoding"}
  }

  body = await request.body()

  async with httpx.AsyncClient(timeout=httpx.Timeout(900.0, connect=30.0)) as client:
    try:
      upstream_req = client.build_request(
        request.method,
        upstream,
        headers=headers,
        content=body if body else None,
      )
      upstream_res = await client.send(upstream_req, stream=True)
    except httpx.HTTPError as exc:
      raise HTTPException(status_code=502, detail=f"tenant unreachable: {exc}") from exc

  out_headers = {
    k: v
    for k, v in upstream_res.headers.items()
    if k.lower() not in {"content-encoding", "transfer-encoding", "connection"}
  }

  if upstream_res.headers.get("content-type", "").startswith("text/event-stream"):
    return StreamingResponse(
      upstream_res.aiter_bytes(),
      status_code=upstream_res.status_code,
      headers=out_headers,
      media_type=upstream_res.headers.get("content-type"),
    )

  content = await upstream_res.aread()
  await upstream_res.aclose()
  return Response(content=content, status_code=upstream_res.status_code, headers=out_headers)
