"""Detect local (or tenant-pod) dev servers and reverse-proxy them for the web UI."""

from __future__ import annotations

import socket
from http.client import HTTPConnection
from typing import Any
from urllib.error import URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen

# Common dev-server ports (GemCode web UI uses 3002; API 3001)
COMMON_DEV_PORTS = (
  3000,
  3003,
  3004,
  5173,
  5174,
  8080,
  8000,
  4200,
  5000,
  4321,
  8888,
)

_STRIP_RESPONSE_HEADERS = frozenset(
  {
    "transfer-encoding",
    "connection",
    "content-length",
    "content-encoding",
    "x-frame-options",
    "content-security-policy",
    "content-security-policy-report-only",
  }
)


def _port_open(host: str, port: int, *, timeout: float = 0.2) -> bool:
  try:
    with socket.create_connection((host, port), timeout=timeout):
      return True
  except OSError:
    return False


def _probe_http(url: str, *, timeout: float = 1.5) -> dict[str, Any] | None:
  try:
    req = Request(url, headers={"User-Agent": "GemCode-Web-Preview/1.0"})
    with urlopen(req, timeout=timeout) as resp:
      ctype = (resp.headers.get("Content-Type") or "").lower()
      return {
        "ok": True,
        "status": resp.status,
        "content_type": ctype,
      }
  except (URLError, OSError, TimeoutError, ValueError):
    return None


def scan_local_preview_ports(
  *,
  host: str = "127.0.0.1",
  extra_ports: list[int] | None = None,
) -> list[dict[str, Any]]:
  """Return listening HTTP-ish ports on localhost (tenant pod when hosted)."""
  ports: list[int] = list(COMMON_DEV_PORTS)
  if extra_ports:
    for p in extra_ports:
      if isinstance(p, int) and 1 < p < 65536 and p not in ports:
        ports.append(p)

  found: list[dict[str, Any]] = []
  for port in ports:
    if not _port_open(host, port):
      continue
    url = f"http://{host}:{port}"
    probe = _probe_http(url)
    found.append(
      {
        "port": port,
        "url": url,
        "reachable": probe is not None,
        "content_type": (probe or {}).get("content_type"),
      }
    )
  return found


def handle_preview_get(kind: str) -> tuple[int, dict[str, Any]]:
  k = (kind or "ports").strip().lower()
  if k == "ports":
    ports = scan_local_preview_ports()
    return 200, {"ok": True, "ports": ports, "host": "127.0.0.1"}
  return 400, {"ok": False, "error": f"Unknown preview kind: {kind}"}


def _inject_base_href(html: str, base_path: str) -> str:
  base_tag = f'<base href="{base_path}">'
  if "<base " in html.lower():
    return html
  lower = html.lower()
  idx = lower.find("<head")
  if idx >= 0:
    gt = html.find(">", idx)
    if gt >= 0:
      return html[: gt + 1] + base_tag + html[gt + 1 :]
  return base_tag + html


def handle_preview_proxy(
  port: int,
  sub_path: str = "/",
  *,
  query: str = "",
) -> tuple[int, bytes, dict[str, str]]:
  """
  Reverse-proxy HTTP from 127.0.0.1:{port} (on this process / tenant pod).

  Returns (status, body, response_headers).
  """
  if not isinstance(port, int) or port < 1 or port > 65535:
    return 400, b'{"error":"Invalid port"}', {"Content-Type": "application/json; charset=utf-8"}

  # Block GemCode's own API/UI ports from being framed as "app preview"
  if port in (3001, 3002):
    return (
      400,
      b'{"error":"Port reserved for GemCode API/UI"}',
      {"Content-Type": "application/json; charset=utf-8"},
    )

  raw = sub_path or "/"
  if not raw.startswith("/"):
    raw = "/" + raw
  # Collapse .. segments
  parts: list[str] = []
  for seg in raw.split("/"):
    if seg in ("", "."):
      continue
    if seg == "..":
      if parts:
        parts.pop()
      continue
    parts.append(seg)
  path = "/" + "/".join(parts)
  if raw.endswith("/") and path != "/":
    path += "/"

  qs = (query or "").lstrip("?")
  request_path = path if not qs else f"{path}?{qs}"

  try:
    conn = HTTPConnection("127.0.0.1", port, timeout=20.0)
    conn.request(
      "GET",
      request_path,
      headers={"User-Agent": "GemCode-Web-Preview/1.0", "Accept": "*/*"},
    )
    resp = conn.getresponse()
    body = resp.read()
    ctype = (resp.getheader("Content-Type") or "").lower()
    headers: dict[str, str] = {}
    for key, value in resp.getheaders():
      if key.lower() in _STRIP_RESPONSE_HEADERS:
        continue
      headers[key] = value
    status = int(resp.status)
    conn.close()
  except OSError as exc:
    msg = f'{{"error":"Preview upstream unreachable: {type(exc).__name__}: {exc}"}}'
    return 502, msg.encode(), {"Content-Type": "application/json; charset=utf-8"}

  proxy_base = f"/api/preview/proxy/{port}/"
  if "text/html" in ctype:
    try:
      text = body.decode("utf-8", errors="replace")
      text = _inject_base_href(text, proxy_base)
      body = text.encode("utf-8")
      headers["Content-Type"] = "text/html; charset=utf-8"
    except Exception:
      pass

  headers["Cache-Control"] = "no-store"
  headers["X-Frame-Options"] = "SAMEORIGIN"
  headers["Content-Security-Policy"] = (
    "default-src * 'unsafe-inline' 'unsafe-eval' data: blob:; "
    "script-src * 'unsafe-inline' 'unsafe-eval'; style-src * 'unsafe-inline'; "
    "frame-ancestors 'self';"
  )
  return status, body, headers


def parse_preview_proxy_path(raw_path: str) -> tuple[int, str] | None:
  """Parse `/api/preview/proxy/{port}` or `/api/preview/proxy/{port}/...` → (port, sub_path)."""
  prefix = "/api/preview/proxy/"
  if not raw_path.startswith(prefix):
    return None
  rest = raw_path[len(prefix) :]
  if not rest:
    return None
  parts = rest.split("/", 1)
  try:
    port = int(parts[0])
  except ValueError:
    return None
  sub = "/"
  if len(parts) > 1 and parts[1]:
    sub = "/" + unquote(parts[1])
  elif rest.endswith("/") or raw_path.endswith("/"):
    sub = "/"
  return port, sub
