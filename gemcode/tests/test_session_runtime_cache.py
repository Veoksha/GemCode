"""Tests for Gemini context-cache cleanup heuristics (session_runtime)."""

from __future__ import annotations

import pytest

from gemcode.session_runtime import _gemini_cache_delete_already_gone


def test_cache_delete_harmless_user_reported_403() -> None:
  pytest.importorskip("google.genai.errors")
  from google.genai.errors import ClientError

  exc = ClientError(
    403,
    {"error": {"message": "CachedContent not found (or permission denied)", "status": "PERMISSION_DENIED"}},
    None,
  )
  assert _gemini_cache_delete_already_gone(exc) is True


def test_cache_delete_not_harmless_other_403() -> None:
  pytest.importorskip("google.genai.errors")
  from google.genai.errors import ClientError

  exc = ClientError(
    403,
    {"error": {"message": "Permission denied on billing account", "status": "PERMISSION_DENIED"}},
    None,
  )
  assert _gemini_cache_delete_already_gone(exc) is False


def test_cache_delete_harmless_404() -> None:
  pytest.importorskip("google.genai.errors")
  from google.genai.errors import ClientError

  exc = ClientError(404, {"error": {"message": "Not found", "status": "NOT_FOUND"}}, None)
  assert _gemini_cache_delete_already_gone(exc) is True
