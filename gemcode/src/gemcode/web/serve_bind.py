"""Bind helpers for ``gemcode serve`` (port scan + user-facing connection hints)."""

from __future__ import annotations

import errno
import os
import socket
from http.server import ThreadingHTTPServer
from typing import Callable, TypeVar

from gemcode.web.serve_state import DEFAULT_SERVE_PORT, serve_base_url

# Next.js dev server default — skip when auto-picking an API port.
UI_RESERVED_PORTS: frozenset[int] = frozenset({3002})

HandlerFactory = TypeVar("HandlerFactory", bound=Callable[..., type])


def iter_candidate_ports(
  preferred: int,
  *,
  max_attempts: int | None = None,
  skip: frozenset[int] = UI_RESERVED_PORTS,
) -> list[int]:
  """Ports to try, starting at *preferred*, skipping reserved UI ports."""
  attempts = max_attempts
  if attempts is None:
    attempts = int(os.environ.get("GEMCODE_WEB_PORT_SCAN", "30"))
  ports: list[int] = []
  port = preferred
  while len(ports) < attempts:
    while port in skip:
      port += 1
    ports.append(port)
    port += 1
  return ports


def bind_http_server(
  host: str,
  handler_factory: HandlerFactory,
  preferred_port: int,
  *,
  max_attempts: int | None = None,
) -> tuple[ThreadingHTTPServer, int]:
  """Bind ``ThreadingHTTPServer``, advancing to the next port if busy."""
  last_err: OSError | None = None
  for port in iter_candidate_ports(preferred_port, max_attempts=max_attempts):
    try:
      httpd = ThreadingHTTPServer((host, port), handler_factory)
      return httpd, port
    except OSError as exc:
      if exc.errno != errno.EADDRINUSE:
        raise
      last_err = exc
  raise OSError(
    errno.EADDRINUSE,
    f"No free port near {preferred_port} after {len(iter_candidate_ports(preferred_port, max_attempts=max_attempts))} attempts",
  ) from last_err


def port_is_free(host: str, port: int) -> bool:
  try:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
      sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      sock.bind((host, port))
    return True
  except OSError:
    return False


def find_available_port(
  host: str,
  preferred_port: int,
  *,
  max_attempts: int | None = None,
) -> int:
  """Return the first bindable port without starting a server."""
  for port in iter_candidate_ports(preferred_port, max_attempts=max_attempts):
    if port_is_free(host, port):
      return port
  raise OSError(
    errno.EADDRINUSE,
    f"No free port near {preferred_port}",
  )


def format_connection_lines(
  host: str,
  actual_port: int,
  *,
  preferred_port: int = DEFAULT_SERVE_PORT,
) -> list[str]:
  """Human-readable instructions for API + web UI wiring."""
  url = serve_base_url(host, actual_port)
  lines: list[str] = []
  if actual_port != preferred_port:
    lines.append(
      f"Port {preferred_port} is busy — GemCode API is on {url} instead."
    )
  else:
    lines.append(f"GemCode API listening on {url}")
  lines.append("Web UI: run `npm run dev` in gemcode-web-ui → http://localhost:3002")
  lines.append(f"Point the UI at this API (gemcode-web-ui/.env.local):")
  lines.append(f"  NEXT_PUBLIC_API_URL={url}")
  return lines
