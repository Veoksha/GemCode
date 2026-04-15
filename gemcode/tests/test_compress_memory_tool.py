from __future__ import annotations

from pathlib import Path

import pytest

from gemcode.config import GemCodeConfig
from gemcode.tools.compress_memory import make_compress_memory_tool


def test_compress_memory_refuses_sensitive_name(tmp_path: Path):
  root = tmp_path
  (root / ".gemcode").mkdir(parents=True, exist_ok=True)
  p = root / ".gemcode" / "credentials.md"
  p.write_text("some text", encoding="utf-8")

  cfg = GemCodeConfig(project_root=root)
  tool = make_compress_memory_tool(cfg)

  res = tool(str(p.relative_to(root)))
  assert res["ok"] is False
  assert "sensitive" in (res.get("error") or "").lower()


def test_compress_memory_refuses_non_markdown(tmp_path: Path):
  root = tmp_path
  p = root / "script.py"
  p.write_text("print('hi')", encoding="utf-8")

  cfg = GemCodeConfig(project_root=root)
  tool = make_compress_memory_tool(cfg)

  res = tool("script.py")
  assert res["ok"] is False
  assert "markdown-like" in (res.get("error") or "").lower()


@pytest.mark.parametrize("name", ["notes.md", "README.txt"])
def test_compress_memory_requires_api_key_when_called(tmp_path: Path, monkeypatch, name: str):
  root = tmp_path
  p = root / name
  p.write_text("# Title\n\nHello world.\n", encoding="utf-8")

  cfg = GemCodeConfig(project_root=root)
  tool = make_compress_memory_tool(cfg)

  monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

  res = tool(name)
  assert res["ok"] is False
  assert "google_api_key" in (res.get("error") or "").lower()

