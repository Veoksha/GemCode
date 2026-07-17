"""Detect local (or tenant-pod) dev servers and reverse-proxy them for the web UI."""

from __future__ import annotations

import atexit
import socket
import subprocess
import sys
import threading
import time
from http.client import HTTPConnection
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote, unquote
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
  5500,
)

# When nothing listens, auto-start ``python -m http.server`` for static HTML previews.
# Skip framework ports (Vite/Next) so we do not mask a missing ``npm run dev``.
STATIC_AUTOSTART_PORTS = frozenset({8000, 8080, 5500, 8888, 5000, 4321})

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

_server_lock = threading.Lock()
_managed_servers: dict[int, subprocess.Popen[bytes]] = {}


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


def _proxy_base_for_path(port: int, request_path: str) -> str:
  """``/api/preview/proxy/{port}/…/`` directory for relative assets."""
  path = request_path.split("?", 1)[0] or "/"
  if path.endswith("/"):
    dir_path = path
  elif "/" in path:
    dir_path = path.rsplit("/", 1)[0] + "/"
  else:
    dir_path = "/"
  if not dir_path.startswith("/"):
    dir_path = "/" + dir_path
  # Encode each segment so spaces in folder names work in <base href>
  parts = [quote(seg, safe="") for seg in dir_path.split("/") if seg]
  encoded = "/" + "/".join(parts) + ("/" if parts or dir_path.endswith("/") else "")
  if encoded == "//" or encoded == "":
    encoded = "/"
  elif not encoded.endswith("/"):
    encoded += "/"
  return f"/api/preview/proxy/{port}{encoded}"


def _cleanup_managed_servers() -> None:
  with _server_lock:
    for proc in _managed_servers.values():
      try:
        proc.terminate()
      except Exception:
        pass
    _managed_servers.clear()


atexit.register(_cleanup_managed_servers)


def ensure_workspace_static_server(port: int, root: Path) -> tuple[bool, str | None]:
  """
  Ensure something accepts TCP on ``127.0.0.1:port``.

  If the port is free and eligible, start ``python -m http.server`` in ``root``.
  Returns (ok, note).
  """
  if _port_open("127.0.0.1", port, timeout=0.35):
    return True, None
  if port not in STATIC_AUTOSTART_PORTS:
    return False, (
      f"No server on :{port}. Start your app in the workspace "
      f"(e.g. npm run dev) or use a static port like 8000."
    )
  root = root.expanduser().resolve()
  if not root.is_dir():
    return False, f"Workspace root is not a directory: {root}"

  with _server_lock:
    if _port_open("127.0.0.1", port, timeout=0.35):
      return True, None
    existing = _managed_servers.get(port)
    if existing is not None and existing.poll() is None:
      for _ in range(25):
        if _port_open("127.0.0.1", port, timeout=0.2):
          return True, "managed"
        if existing.poll() is not None:
          break
        time.sleep(0.08)
    try:
      proc = subprocess.Popen(
        [
          sys.executable,
          "-m",
          "http.server",
          str(port),
          "--bind",
          "127.0.0.1",
        ],
        cwd=str(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
      )
    except OSError as exc:
      return False, f"Could not start static preview server: {exc}"
    _managed_servers[port] = proc
    for _ in range(30):
      if _port_open("127.0.0.1", port, timeout=0.2):
        return True, "started"
      if proc.poll() is not None:
        _managed_servers.pop(port, None)
        return False, "Static preview server exited immediately (port may be busy)."
      time.sleep(0.08)
    return False, "Timed out waiting for static preview server."


def handle_preview_proxy(
  port: int,
  sub_path: str = "/",
  *,
  query: str = "",
  workspace_root: str | Path | None = None,
) -> tuple[int, bytes, dict[str, str]]:
  """
  Reverse-proxy HTTP from 127.0.0.1:{port} (on this process / tenant pod).

  If nothing is listening and ``port`` is a static preview port, auto-start
  ``python -m http.server`` in the workspace so HTML apps work without a
  manual ``bash`` step (hosted GKE).

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

  # Encode path for the upstream request (spaces in folder names).
  upstream_path = "/" + "/".join(quote(seg, safe="") for seg in parts)
  if path.endswith("/") and upstream_path != "/":
    upstream_path += "/"
  if not qs:
    upstream_request = upstream_path
  else:
    upstream_request = f"{upstream_path}?{qs}"

  root: Path | None = None
  if workspace_root:
    try:
      root = Path(workspace_root).expanduser().resolve()
    except OSError:
      root = None

  def _fetch() -> tuple[int, bytes, str, list[tuple[str, str]]]:
    conn = HTTPConnection("127.0.0.1", port, timeout=20.0)
    conn.request(
      "GET",
      upstream_request,
      headers={"User-Agent": "GemCode-Web-Preview/1.0", "Accept": "*/*"},
    )
    resp = conn.getresponse()
    body = resp.read()
    ctype = (resp.getheader("Content-Type") or "").lower()
    headers_list = resp.getheaders()
    status = int(resp.status)
    conn.close()
    return status, body, ctype, headers_list

  try:
    status, body, ctype, headers_list = _fetch()
  except OSError as first_exc:
    note: str | None = None
    if root is not None:
      ok, note = ensure_workspace_static_server(port, root)
      if ok:
        try:
          time.sleep(0.05)
          status, body, ctype, headers_list = _fetch()
        except OSError as second_exc:
          msg = (
            f'{{"error":"Preview upstream unreachable after starting static server: '
            f'{type(second_exc).__name__}: {second_exc}"}}'
          )
          return 502, msg.encode(), {"Content-Type": "application/json; charset=utf-8"}
      else:
        detail = note or str(first_exc)
        msg = f'{{"error":"Preview upstream unreachable: {detail}"}}'
        return 502, msg.encode(), {"Content-Type": "application/json; charset=utf-8"}
    else:
      msg = f'{{"error":"Preview upstream unreachable: {type(first_exc).__name__}: {first_exc}"}}'
      return 502, msg.encode(), {"Content-Type": "application/json; charset=utf-8"}

  headers: dict[str, str] = {}
  for key, value in headers_list:
    if key.lower() in _STRIP_RESPONSE_HEADERS:
      continue
    headers[key] = value

  proxy_base = _proxy_base_for_path(port, path)
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
