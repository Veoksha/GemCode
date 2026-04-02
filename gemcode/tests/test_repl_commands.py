"""Tests for REPL slash-command formatters."""

from __future__ import annotations

from pathlib import Path

from gemcode.config import GemCodeConfig
from gemcode.repl_commands import (
  format_audit_lines,
  format_doctor_lines,
  format_hooks_lines,
  format_memory_lines,
  format_model_lines,
  format_permissions_lines,
)


def test_format_doctor_contains_python() -> None:
  cfg = GemCodeConfig(project_root=Path("."))
  lines = format_doctor_lines(cfg)
  assert any("python:" in ln for ln in lines)


def test_format_model_lines() -> None:
  cfg = GemCodeConfig(project_root=Path("."))
  cfg.model = "gemini-2.5-flash"
  lines = format_model_lines(cfg)
  assert any("gemini-2.5-flash" in ln for ln in lines)


def test_format_permissions_lines() -> None:
  cfg = GemCodeConfig(project_root=Path("."))
  lines = format_permissions_lines(cfg)
  assert any("permission_mode" in ln for ln in lines)


def test_format_memory_hooks_lines() -> None:
  cfg = GemCodeConfig(project_root=Path("."))
  assert any("memories" in ln.lower() for ln in format_memory_lines(cfg))
  assert any("post_turn" in ln or "hooks" in ln.lower() for ln in format_hooks_lines(cfg))


def test_format_audit_lines(tmp_path: Path) -> None:
  root = tmp_path / "proj"
  (root / ".gemcode").mkdir(parents=True)
  (root / ".gemcode" / "audit.log").write_text(
      "a\nb\nc\nd\n", encoding="utf-8"
  )
  cfg = GemCodeConfig(project_root=root)
  lines = format_audit_lines(cfg, tail=2)
  joined = "\n".join(lines)
  assert "c" in joined and "d" in joined
