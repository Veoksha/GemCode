"""Tests for preview proxy path parsing and static autostart."""

from __future__ import annotations

from pathlib import Path

from gemcode.web import preview_api
from gemcode.web.preview_api import parse_preview_proxy_path


def test_parse_preview_proxy_root() -> None:
  assert parse_preview_proxy_path("/api/preview/proxy/8000") == (8000, "/")
  assert parse_preview_proxy_path("/api/preview/proxy/8000/") == (8000, "/")


def test_parse_preview_proxy_subpath() -> None:
  assert parse_preview_proxy_path("/api/preview/proxy/5173/assets/app.js") == (
    5173,
    "/assets/app.js",
  )


def test_parse_preview_proxy_space_path() -> None:
  assert parse_preview_proxy_path("/api/preview/proxy/8000/final%20todo/index.html") == (
    8000,
    "/final todo/index.html",
  )


def test_parse_preview_proxy_invalid() -> None:
  assert parse_preview_proxy_path("/api/preview") is None
  assert parse_preview_proxy_path("/api/preview/proxy/") is None
  assert parse_preview_proxy_path("/api/preview/proxy/abc") is None


def test_proxy_base_for_nested_html() -> None:
  assert preview_api._proxy_base_for_path(8000, "/final todo/index.html") == (
    "/api/preview/proxy/8000/final%20todo/"
  )


def test_static_autostart_serves_workspace(tmp_path: Path) -> None:
  (tmp_path / "hello.html").write_text("<html><body>hi</body></html>", encoding="utf-8")
  port = 18765
  original = preview_api.STATIC_AUTOSTART_PORTS
  preview_api.STATIC_AUTOSTART_PORTS = frozenset(set(original) | {port})
  try:
    status, body, headers = preview_api.handle_preview_proxy(
      port,
      "/hello.html",
      workspace_root=tmp_path,
    )
    assert status == 200
    assert b"hi" in body
    assert "text/html" in headers.get("Content-Type", "")
  finally:
    preview_api.STATIC_AUTOSTART_PORTS = original
    with preview_api._server_lock:
      proc = preview_api._managed_servers.pop(port, None)
    if proc is not None and proc.poll() is None:
      proc.terminate()
      try:
        proc.wait(timeout=2)
      except Exception:
        proc.kill()
