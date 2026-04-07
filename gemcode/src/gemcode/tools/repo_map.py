"""
Repo map tool: lightweight symbol-first context for large repos.

Inspired by Aider's "repo map" approach: provide a compact overview (files +
top-level symbols) under a strict token/char budget, then read specific files
on demand.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig
from gemcode.paths import PathEscapeError, resolve_under_root
from gemcode.trust import is_trusted_root


_PY_DEF = re.compile(r"^\s*(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
_TS_DEF = re.compile(
  r"^\s*(export\s+)?(async\s+)?(function|class|interface|type)\s+([A-Za-z_][A-Za-z0-9_]*)"
)


def _iter_files(root: Path, include_glob: str) -> list[Path]:
  out: list[Path] = []
  for p in root.glob(include_glob):
    if not p.is_file():
      continue
    if "/.git/" in str(p):
      continue
    # Skip huge files; repo_map is meant to be cheap.
    try:
      if p.stat().st_size > 300_000:
        continue
    except OSError:
      continue
    out.append(p)
    if len(out) >= 800:
      break
  return out


def _symbols_for_file(p: Path, *, max_lines: int = 400) -> list[str]:
  try:
    text = p.read_text(encoding="utf-8", errors="ignore")
  except OSError:
    return []
  lines = text.splitlines()[:max_lines]
  syms: list[str] = []
  for ln in lines:
    m = _PY_DEF.match(ln)
    if m:
      syms.append(f"{m.group(1)} {m.group(2)}")
      continue
    m2 = _TS_DEF.match(ln)
    if m2:
      syms.append(f"{m2.group(3)} {m2.group(4)}")
  # Deduplicate while preserving order
  seen: set[str] = set()
  out: list[str] = []
  for s in syms:
    if s in seen:
      continue
    seen.add(s)
    out.append(s)
    if len(out) >= 40:
      break
  return out


def make_repo_map_tool(cfg: GemCodeConfig):
  root = cfg.project_root
  trusted = is_trusted_root(root)

  def repo_map(
    path: str = ".",
    include_glob: str = "**/*.{py,ts,tsx,js,jsx,md,txt,json,yml,yaml}",
    max_chars: int = 18_000,
    max_files: int = 200,
    include_symbols: bool = True,
  ) -> dict[str, Any]:
    """
    Return a compact overview of a repo subtree under a strict char budget.

    Best for: large codebases where sending many full files is expensive.
    Use this, then `read_file` for specific files.
    """
    if not trusted:
      return {"error": "Project folder is not trusted. Re-run GemCode and approve folder trust."}
    try:
      base = resolve_under_root(root, path)
    except PathEscapeError as e:
      return {"error": str(e)}
    if not base.is_dir():
      return {"error": f"Not a directory: {path}"}

    files = _iter_files(base, include_glob)
    # Make paths relative to project root for stable references
    rels: list[str] = []
    for p in files:
      try:
        rels.append(str(p.resolve().relative_to(root)))
      except ValueError:
        continue
      if len(rels) >= max_files:
        break

    # Build a char-budgeted map string.
    lines: list[str] = []
    lines.append(f"Repo map for: {path} (files={len(rels)})")
    lines.append("")
    for rel in rels:
      if sum(len(x) + 1 for x in lines) >= max_chars:
        break
      lines.append(rel)
      if include_symbols and rel.endswith((".py", ".ts", ".tsx", ".js", ".jsx")):
        sym = _symbols_for_file(root / rel)
        for s in sym:
          if sum(len(x) + 1 for x in lines) >= max_chars:
            break
          lines.append(f"  - {s}")

    out = "\n".join(lines)
    truncated = len(out) > max_chars
    if truncated:
      out = out[:max_chars] + "\n… [truncated]"
    return {"path": path, "map": out, "truncated": truncated}

  return repo_map

