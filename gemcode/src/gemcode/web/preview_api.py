"""Detect local dev servers for in-UI localhost preview."""

from __future__ import annotations

import socket
from typing import Any
from urllib.error import URLError
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
  """Return listening HTTP-ish ports on localhost."""
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
