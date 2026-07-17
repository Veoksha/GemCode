"""Selected project folder must remain cfg.project_root (not fleet_root)."""

from __future__ import annotations

import json
from pathlib import Path

from gemcode.org import resolve_fleet_root


def test_resolve_fleet_root_walks_up_to_tenant(tmp_path: Path) -> None:
  tenant = tmp_path / "workspace"
  project = tenant / "final todo"
  project.mkdir(parents=True)
  (tenant / ".gemcode").mkdir()
  (tenant / ".gemcode" / "org.json").write_text("{}", encoding="utf-8")

  assert resolve_fleet_root(project).resolve() == tenant.resolve()
  assert resolve_fleet_root(tenant).resolve() == tenant.resolve()


def test_inject_context_scopes_to_project_folder(tmp_path: Path) -> None:
  from gemcode.config import GemCodeConfig
  from gemcode.web import sse_adapter

  project = tmp_path / "my-app"
  project.mkdir()
  cfg = GemCodeConfig(project_root=project)
  object.__setattr__(cfg, "_web_workspace_mode", "code")
  object.__setattr__(cfg, "_web_interactive_hitl", True)
  out = sse_adapter._inject_web_code_context(cfg, "analyze codebase", {})
  assert str(project.resolve()) in out
  assert "stay **inside this workspace root only**" in out
