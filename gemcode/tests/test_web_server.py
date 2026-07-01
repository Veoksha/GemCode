"""Smoke tests for ``gemcode serve`` HTTP handlers."""

from __future__ import annotations

import json
import threading
import tempfile
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from gemcode.web.server import _build_handler


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
  finally:
    httpd.shutdown()
