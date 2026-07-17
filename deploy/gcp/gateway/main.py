"""Tenant HTTP gateway — routes /t/{tenant_id}/* to per-user gemcode serve pods."""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

app = FastAPI(title="GemCode Tenant Gateway", version="0.1.0")

TENANTS_NS = os.environ.get("TENANTS_NAMESPACE", "gemcode-tenants")
TENANT_ID_RE = re.compile(r"^u-[a-f0-9]{16}$")

HOP_HEADERS = frozenset(
  {"host", "content-length", "connection", "transfer-encoding", "content-encoding"}
)

# Reuse one client — streaming responses must outlive the proxy handler.
_HTTP = httpx.AsyncClient(timeout=httpx.Timeout(900.0, connect=30.0))


@app.on_event("shutdown")
async def _shutdown() -> None:
  await _HTTP.aclose()


def _tenant_base(tenant_id: str) -> str:
  if not TENANT_ID_RE.fullmatch(tenant_id):
    raise HTTPException(status_code=400, detail="invalid tenant id")
  return f"http://gemcode-tenant-{tenant_id}.{TENANTS_NS}.svc.cluster.local:3001"


def _filter_headers(headers: httpx.Headers) -> dict[str, str]:
  return {
    k: v
    for k, v in headers.items()
    if k.lower() not in HOP_HEADERS
  }


def _wants_stream(path: str, method: str) -> bool:
  return method.upper() == "POST" and path.rstrip("/") == "api/chat"


async def _stream_chat(
  method: str,
  upstream: str,
  headers: dict[str, str],
  body: bytes | None,
) -> StreamingResponse:
  """Proxy SSE chat without closing the upstream socket early."""
  try:
    upstream_req = _HTTP.build_request(
      method,
      upstream,
      headers=headers,
      content=body if body else None,
    )
    upstream_res = await _HTTP.send(upstream_req, stream=True)
  except httpx.HTTPError as exc:
    raise HTTPException(status_code=502, detail=f"tenant unreachable: {exc}") from exc

  async def body_iter() -> AsyncIterator[bytes]:
    try:
      async for chunk in upstream_res.aiter_bytes():
        yield chunk
    finally:
      await upstream_res.aclose()

  return StreamingResponse(
    body_iter(),
    status_code=upstream_res.status_code,
    headers=_filter_headers(upstream_res.headers),
    media_type=upstream_res.headers.get("content-type"),
  )


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
    if k.lower() not in HOP_HEADERS
  }

  body = await request.body()

  try:
    if _wants_stream(path, request.method):
      return await _stream_chat(request.method, upstream, headers, body if body else None)

    upstream_res = await _HTTP.request(
      request.method,
      upstream,
      headers=headers,
      content=body if body else None,
    )
  except httpx.HTTPError as exc:
    raise HTTPException(status_code=502, detail=f"tenant unreachable: {exc}") from exc

  return Response(
    content=upstream_res.content,
    status_code=upstream_res.status_code,
    headers=_filter_headers(upstream_res.headers),
  )
