"""Web UI permissions must override process-level GEMCODE_SUPER_MODE."""

from __future__ import annotations

from pathlib import Path

from gemcode.config import GemCodeConfig, apply_super_mode
from gemcode.web import sse_adapter


def test_web_permissions_clear_env_super_mode(tmp_path: Path, monkeypatch) -> None:
  monkeypatch.setenv("GEMCODE_SUPER_MODE", "1")
  cfg = GemCodeConfig(project_root=tmp_path)
  assert cfg.super_mode is True
  apply_super_mode(cfg)
  assert cfg.yes_to_all is True

  sse_adapter._configure_web_permissions(
    cfg,
    {
      "permissions": {
        "super_mode": False,
        "auto_approve": {
          "bash": False,
          "file_read": False,
          "file_write": False,
          "web_search": False,
        },
      }
    },
  )
  assert cfg.super_mode is False
  assert cfg.yes_to_all is False
  assert getattr(cfg, "_web_interactive_hitl") is True


def test_web_permissions_ui_super_mode_on(tmp_path: Path, monkeypatch) -> None:
  monkeypatch.delenv("GEMCODE_SUPER_MODE", raising=False)
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.super_mode = False
  cfg.yes_to_all = False

  sse_adapter._configure_web_permissions(
    cfg,
    {"permissions": {"super_mode": True, "auto_approve": {}}},
  )
  assert cfg.super_mode is True
  assert cfg.yes_to_all is True
  assert getattr(cfg, "_web_interactive_hitl") is False


def test_inject_web_context_auto_approve_copy(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.yes_to_all = True
  object.__setattr__(cfg, "_web_workspace_mode", "code")
  object.__setattr__(cfg, "_web_interactive_hitl", False)
  out = sse_adapter._inject_web_code_context(cfg, "hello", {})
  assert "AUTO-APPROVE is ON" in out
  assert "Never mention Approve" in out
  assert "inline Yes/No card" not in out


def test_inject_web_context_interactive_copy(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.super_mode = False
  cfg.yes_to_all = False
  object.__setattr__(cfg, "_web_workspace_mode", "code")
  object.__setattr__(cfg, "_web_interactive_hitl", True)
  out = sse_adapter._inject_web_code_context(cfg, "hello", {})
  assert "inline Yes/No card" in out
  assert "AUTO-APPROVE is ON" not in out
