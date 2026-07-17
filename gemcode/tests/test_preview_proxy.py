"""Tests for preview proxy path parsing."""

from gemcode.web.preview_api import parse_preview_proxy_path


def test_parse_preview_proxy_root() -> None:
  assert parse_preview_proxy_path("/api/preview/proxy/8000") == (8000, "/")
  assert parse_preview_proxy_path("/api/preview/proxy/8000/") == (8000, "/")


def test_parse_preview_proxy_subpath() -> None:
  assert parse_preview_proxy_path("/api/preview/proxy/5173/assets/app.js") == (
    5173,
    "/assets/app.js",
  )


def test_parse_preview_proxy_invalid() -> None:
  assert parse_preview_proxy_path("/api/preview") is None
  assert parse_preview_proxy_path("/api/preview/proxy/") is None
  assert parse_preview_proxy_path("/api/preview/proxy/abc") is None
