"""Tests for model error formatting."""

from __future__ import annotations

import pytest

from gemcode.model_errors import format_model_error_for_user


def test_format_generic_exception() -> None:
  msg = format_model_error_for_user(RuntimeError("something broke"))
  assert "RuntimeError" in msg
  assert "something broke" in msg


def test_format_genai_client_error() -> None:
  try:
    from google.genai import errors as genai_errors
  except ImportError:
    pytest.skip("google.genai not installed")
  err = genai_errors.ClientError(
      429,
      {"error": {"message": "Too many requests", "status": "RESOURCE_EXHAUSTED"}},
      None,
  )
  msg = format_model_error_for_user(err)
  assert "429" in msg
  assert "Rate limited" in msg or "rate" in msg.lower()
