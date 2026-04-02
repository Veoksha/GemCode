"""Tests for persisted API credentials."""

from __future__ import annotations

import json
import os

import pytest

from gemcode.credentials import (
  apply_saved_google_api_key_to_environ,
  credentials_path,
  load_saved_google_api_key,
  save_google_api_key_to_user_store,
)


def test_save_load_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
  monkeypatch.setenv("GEMCODE_HOME", str(tmp_path))
  save_google_api_key_to_user_store("test-key-abc")
  assert load_saved_google_api_key() == "test-key-abc"
  p = credentials_path()
  assert p.is_file()
  data = json.loads(p.read_text(encoding="utf-8"))
  assert data.get("google_api_key") == "test-key-abc"


def test_apply_saved_does_not_override_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
  monkeypatch.setenv("GEMCODE_HOME", str(tmp_path))
  save_google_api_key_to_user_store("from-file")
  monkeypatch.setenv("GOOGLE_API_KEY", "from-env")
  apply_saved_google_api_key_to_environ()
  assert os.environ["GOOGLE_API_KEY"] == "from-env"


def test_apply_saved_sets_when_unset(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
  monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
  monkeypatch.setenv("GEMCODE_HOME", str(tmp_path))
  save_google_api_key_to_user_store("only-file")
  apply_saved_google_api_key_to_environ()
  assert os.environ["GOOGLE_API_KEY"] == "only-file"
