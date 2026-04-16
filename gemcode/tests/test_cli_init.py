"""First-run project initialization (.gemcode/)."""

from __future__ import annotations

from pathlib import Path

from gemcode.cli import _initialize_gemcode_project
from gemcode.config import GemCodeConfig


def test_initialize_gemcode_project_creates_dot_gemcode(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  assert not (tmp_path / ".gemcode").exists()
  _initialize_gemcode_project(cfg)
  assert (tmp_path / ".gemcode").is_dir()
  assert (tmp_path / "gemcode.md").is_file()


def test_initialize_gemcode_project_idempotent(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  _initialize_gemcode_project(cfg)
  _initialize_gemcode_project(cfg)
  assert (tmp_path / ".gemcode").is_dir()
  assert (tmp_path / "gemcode.md").is_file()
