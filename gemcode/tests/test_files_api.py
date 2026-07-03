"""Tests for workspace files API."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from gemcode.web.files_api import (
  handle_files_read_get,
  handle_files_tree_get,
  handle_files_write_post,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
  (tmp_path / "hello.txt").write_text("hi\n", encoding="utf-8")
  (tmp_path / "src").mkdir()
  (tmp_path / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
  return tmp_path


def test_files_tree_lists_workspace(workspace: Path) -> None:
  status, payload = handle_files_tree_get(str(workspace))
  assert status == 200
  names = {n["name"] for n in payload["tree"]}
  assert "hello.txt" in names
  assert "src" in names


def test_files_read_and_write(workspace: Path) -> None:
  status, payload = handle_files_read_get(str(workspace), "hello.txt")
  assert status == 200
  assert payload["content"] == "hi\n"

  status, payload = handle_files_write_post(
    {"path": "new.md", "content": "# test"},
    str(workspace),
  )
  assert status == 200
  assert payload["ok"] is True
  assert (workspace / "new.md").read_text(encoding="utf-8") == "# test"


def test_files_reject_path_outside_hosted_root(
  workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  outside = tempfile.mkdtemp()
  monkeypatch.setenv("GEMCODE_HOSTED_TENANT_ROOT", str(workspace))
  status, payload = handle_files_read_get(str(workspace), f"../{Path(outside).name}/x")
  assert status == 403
