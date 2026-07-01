"""Project config snapshots for the GemCode web UI (rules, styles, memory)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from gemcode.output_styles import discover_output_styles
from gemcode.rules import discover_rules


from gemcode.web.project_root import resolve_web_project_root


def _resolve_root(raw_path: str | None) -> Path:
  return resolve_web_project_root(raw_path)


def _memory_snapshot(project_root: Path) -> dict[str, Any]:
  memories = project_root / ".gemcode" / "memories.jsonl"
  curated = project_root / "GEMCODE_MEMORY.md"
  if not curated.is_file():
    curated = project_root / ".gemcode" / "GEMCODE_MEMORY.md"
  lines = 0
  if memories.is_file():
    try:
      lines = sum(1 for ln in memories.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip())
    except OSError:
      pass
  return {
    "memories_file": str(memories),
    "memories_exists": memories.is_file(),
    "memory_entries": lines,
    "curated_file": str(curated) if curated.is_file() else None,
    "curated_exists": curated.is_file(),
  }


def config_snapshot(project_root: Path) -> dict[str, Any]:
  styles = discover_output_styles(project_root)
  style_rows = [{"name": k, "path": str(v)} for k, v in sorted(styles.items())]

  rule_rows: list[dict[str, str]] = []
  for p in discover_rules(project_root):
    try:
      rel = str(p.relative_to(project_root)) if p.is_relative_to(project_root) else str(p)
    except ValueError:
      rel = str(p)
    rule_rows.append({"name": p.stem, "path": rel})

  gemcode_md = project_root / "gemcode.md"
  agent_md = project_root / "AGENTS.md"

  return {
    "ok": True,
    "project_root": str(project_root),
    "output_styles": style_rows,
    "rules": rule_rows,
    "memory": _memory_snapshot(project_root),
    "has_gemcode_md": gemcode_md.is_file(),
    "has_agents_md": agent_md.is_file(),
    "gemcode_md_path": str(gemcode_md) if gemcode_md.is_file() else None,
  }


def handle_config_get(raw_path: str | None) -> tuple[int, dict[str, Any]]:
  root = _resolve_root(raw_path)
  if not root.is_dir():
    return 400, {"ok": False, "error": "project path is not a directory", "path": str(root)}
  return 200, config_snapshot(root)
