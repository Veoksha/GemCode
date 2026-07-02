"""Smoke tests for ``gemcode serve`` HTTP handlers."""

from __future__ import annotations

import json
import socket
import threading
import tempfile
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from gemcode.web.server import _build_handler
from gemcode.web.serve_bind import bind_http_server, find_available_port, iter_candidate_ports


def test_serve_health_endpoint() -> None:
  root = tempfile.mkdtemp()
  handler = _build_handler(root)
  httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
  port = httpd.server_address[1]
  thread = threading.Thread(target=httpd.serve_forever, daemon=True)
  thread.start()
  try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as resp:
      data = json.loads(resp.read())
    assert data["status"] == "ok"
    assert data["service"] == "gemcode-serve"
    assert data["gemcode"] is True
    assert data["project_root"] == str(Path(root).resolve())
    assert data["port"] == port
    assert f":{port}" in data["url"]
  finally:
    httpd.shutdown()


def test_iter_candidate_ports_skips_ui_port() -> None:
  ports = iter_candidate_ports(3001, max_attempts=3)
  assert ports == [3001, 3003, 3004]


def test_bind_http_server_finds_next_port_when_busy() -> None:
  root = tempfile.mkdtemp()
  handler = _build_handler(root)
  blocker = ThreadingHTTPServer(("127.0.0.1", 0), handler)
  busy_port = blocker.server_address[1]
  thread = threading.Thread(target=blocker.serve_forever, daemon=True)
  thread.start()
  try:
    httpd, port = bind_http_server("127.0.0.1", handler, busy_port, max_attempts=5)
    serve_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    serve_thread.start()
    try:
      assert port != busy_port
      with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as resp:
        data = json.loads(resp.read())
      assert data["status"] == "ok"
    finally:
      httpd.shutdown()
  finally:
    blocker.shutdown()


def test_find_available_port() -> None:
  with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    busy = sock.getsockname()[1]
    free = find_available_port("127.0.0.1", busy, max_attempts=5)
    assert free != busy
