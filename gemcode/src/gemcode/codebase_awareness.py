"""
Codebase Awareness — Persistent, incrementally-built project understanding.

Every other coding agent treats each turn as isolated: read files, grep, explore,
then forget. GemCode builds a living understanding of the codebase that compounds
over time.

Three layers:

1. STRUCTURE GRAPH — What files exist, what they export, what imports what.
   Built incrementally from every read_file, grep, list_directory call.
   Not a full AST — just the relationships that matter for navigation.

2. CHANGE JOURNAL — What changed, when, why, and what broke.
   Built from every write_file, search_replace, bash call.
   Enables: "what did we change in the last hour?" and "what might this break?"

3. INSIGHT CACHE — Learned facts about this specific codebase.
   "Tests take 12s to run." "The auth module imports from 3 places."
   "Last time we changed config.py, tests in test_config.py broke."
   Built from tool outcomes, test results, and delegation reports.

The awareness is injected into the agent's context as a compact summary,
replacing the need for repeated exploration. It's like giving the agent
a photographic memory of the project.

Storage: .gemcode/awareness/
  structure.json  — file relationships
  journal.jsonl   — change log
  insights.json   — learned facts
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any


# ── Structure Graph ──────────────────────────────────────────────────────────

def _structure_path(project_root: Path) -> Path:
  d = project_root / ".gemcode" / "awareness"
  d.mkdir(parents=True, exist_ok=True)
  return d / "structure.json"


def _load_structure(project_root: Path) -> dict[str, Any]:
  p = _structure_path(project_root)
  if not p.is_file():
    return {"files": {}, "imports": {}, "exports": {}, "updated_ms": 0}
  try:
    return json.loads(p.read_text(encoding="utf-8"))
  except Exception:
    return {"files": {}, "imports": {}, "exports": {}, "updated_ms": 0}


def _save_structure(project_root: Path, structure: dict[str, Any]) -> None:
  structure["updated_ms"] = int(time.time() * 1000)
  p = _structure_path(project_root)
  p.parent.mkdir(parents=True, exist_ok=True)
  p.write_text(json.dumps(structure, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# Lightweight import/export extraction (no AST, just regex)
_PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE)
_PY_DEF = re.compile(r"^(?:class|def|async\s+def)\s+(\w+)", re.MULTILINE)
_TS_IMPORT = re.compile(r"""(?:import\s+.*?from\s+['"]([^'"]+)['"]|require\s*\(\s*['"]([^'"]+)['"]\s*\))""", re.MULTILINE)
_TS_EXPORT = re.compile(r"^export\s+(?:default\s+)?(?:function|class|const|let|var|interface|type|enum)\s+(\w+)", re.MULTILINE)


def record_file_read(project_root: Path, file_path: str, content: str) -> None:
  """Record structure info when a file is read. Called from after_tool_callback."""
  if not _enabled():
    return
  try:
    structure = _load_structure(project_root)
    files = structure.setdefault("files", {})
    imports = structure.setdefault("imports", {})
    exports = structure.setdefault("exports", {})

    ext = Path(file_path).suffix.lower()
    file_imports: list[str] = []
    file_exports: list[str] = []

    if ext in (".py",):
      for m in _PY_IMPORT.finditer(content[:50_000]):
        mod = m.group(1) or m.group(2)
        if mod:
          file_imports.append(mod)
      for m in _PY_DEF.finditer(content[:50_000]):
        file_exports.append(m.group(1))

    elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs"):
      for m in _TS_IMPORT.finditer(content[:50_000]):
        mod = m.group(1) or m.group(2)
        if mod:
          file_imports.append(mod)
      for m in _TS_EXPORT.finditer(content[:50_000]):
        file_exports.append(m.group(1))

    # Store compact info
    files[file_path] = {
      "size": len(content),
      "lines": content.count("\n") + 1,
      "symbols": len(file_exports),
      "seen_ms": int(time.time() * 1000),
    }
    if file_imports:
      imports[file_path] = file_imports[:50]
    if file_exports:
      exports[file_path] = file_exports[:100]

    # Bound the structure size
    if len(files) > 1000:
      oldest = sorted(files.items(), key=lambda x: x[1].get("seen_ms", 0))[:200]
      for k, _ in oldest:
        files.pop(k, None)
        imports.pop(k, None)
        exports.pop(k, None)

    _save_structure(project_root, structure)
  except Exception:
    pass


# ── Change Journal ───────────────────────────────────────────────────────────

def _journal_path(project_root: Path) -> Path:
  d = project_root / ".gemcode" / "awareness"
  d.mkdir(parents=True, exist_ok=True)
  return d / "journal.jsonl"


def record_change(
  project_root: Path,
  *,
  file_path: str,
  change_type: str,  # "write", "edit", "delete", "bash"
  summary: str = "",
) -> None:
  """Record a change to the journal. Called from after_tool_callback."""
  if not _enabled():
    return
  try:
    entry = {
      "ts_ms": int(time.time() * 1000),
      "file": file_path,
      "type": change_type,
      "summary": summary[:200],
    }
    p = _journal_path(project_root)
    with open(p, "a", encoding="utf-8") as f:
      f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Trim journal if too large (keep last 500 entries)
    _trim_journal(p, max_entries=500)
  except Exception:
    pass


def _trim_journal(path: Path, max_entries: int = 500) -> None:
  try:
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    if len(lines) > max_entries:
      path.write_text("\n".join(lines[-max_entries:]) + "\n", encoding="utf-8")
  except Exception:
    pass


def recent_changes(project_root: Path, limit: int = 20) -> list[dict[str, Any]]:
  """Get recent changes from the journal."""
  p = _journal_path(project_root)
  if not p.is_file():
    return []
  try:
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    entries = []
    for line in reversed(lines):
      try:
        entries.append(json.loads(line))
      except Exception:
        continue
      if len(entries) >= limit:
        break
    return entries
  except Exception:
    return []


# ── Insight Cache ────────────────────────────────────────────────────────────

def _insights_path(project_root: Path) -> Path:
  d = project_root / ".gemcode" / "awareness"
  d.mkdir(parents=True, exist_ok=True)
  return d / "insights.json"


def _load_insights(project_root: Path) -> dict[str, Any]:
  p = _insights_path(project_root)
  if not p.is_file():
    return {"facts": [], "correlations": {}}
  try:
    return json.loads(p.read_text(encoding="utf-8"))
  except Exception:
    return {"facts": [], "correlations": {}}


def _save_insights(project_root: Path, insights: dict[str, Any]) -> None:
  p = _insights_path(project_root)
  p.parent.mkdir(parents=True, exist_ok=True)
  p.write_text(json.dumps(insights, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def record_insight(project_root: Path, fact: str) -> None:
  """Record a learned fact about the codebase."""
  if not _enabled():
    return
  try:
    insights = _load_insights(project_root)
    facts = insights.setdefault("facts", [])
    # Dedup
    if fact in facts:
      return
    facts.append(fact)
    # Bound
    if len(facts) > 200:
      insights["facts"] = facts[-150:]
    _save_insights(project_root, insights)
  except Exception:
    pass


def record_correlation(project_root: Path, cause_file: str, effect_file: str) -> None:
  """Record that changing cause_file tends to affect effect_file."""
  if not _enabled():
    return
  try:
    insights = _load_insights(project_root)
    corr = insights.setdefault("correlations", {})
    effects = corr.setdefault(cause_file, [])
    if effect_file not in effects:
      effects.append(effect_file)
      if len(effects) > 20:
        corr[cause_file] = effects[-15:]
    _save_insights(project_root, insights)
  except Exception:
    pass


def get_affected_files(project_root: Path, changed_file: str) -> list[str]:
  """Given a file that changed, return files likely affected."""
  insights = _load_insights(project_root)
  corr = insights.get("correlations", {})
  direct = corr.get(changed_file, [])

  # Also check reverse imports from structure
  structure = _load_structure(project_root)
  importers: list[str] = []
  changed_module = Path(changed_file).stem
  for file_path, file_imports in structure.get("imports", {}).items():
    for imp in file_imports:
      if changed_module in imp:
        importers.append(file_path)
        break

  return list(dict.fromkeys(direct + importers))[:20]


# ── Context Builder ──────────────────────────────────────────────────────────

def build_awareness_context(project_root: Path, *, max_chars: int = 3000) -> str:
  """
  Build a compact awareness summary for injection into the agent's context.

  This replaces the need for repeated exploration. The agent starts each turn
  knowing the project structure, recent changes, and learned facts.
  """
  if not _enabled():
    return ""

  parts: list[str] = []

  # Structure summary
  structure = _load_structure(project_root)
  files = structure.get("files", {})
  if files:
    # Top files by recent access
    recent_files = sorted(files.items(), key=lambda x: x[1].get("seen_ms", 0), reverse=True)[:15]
    file_lines = []
    for fp, info in recent_files:
      symbols = info.get("symbols", 0)
      lines = info.get("lines", 0)
      file_lines.append(f"  {fp} ({lines}L, {symbols} symbols)")
    if file_lines:
      parts.append("Known files:\n" + "\n".join(file_lines))

  # Recent changes
  changes = recent_changes(project_root, limit=8)
  if changes:
    change_lines = []
    for c in changes:
      change_lines.append(f"  {c.get('type', '?')}: {c.get('file', '?')} — {c.get('summary', '')[:80]}")
    parts.append("Recent changes:\n" + "\n".join(change_lines))

  # Insights
  insights = _load_insights(project_root)
  facts = insights.get("facts", [])
  if facts:
    recent_facts = facts[-8:]
    parts.append("Learned facts:\n" + "\n".join(f"  - {f}" for f in recent_facts))

  if not parts:
    return ""

  text = "[Codebase awareness]\n" + "\n".join(parts)
  return text[:max_chars]


# ── Tool Result Enrichment ───────────────────────────────────────────────────

def enrich_grep_result(
  project_root: Path,
  file_path: str,
  matches: list[dict],
) -> list[dict]:
  """
  Enrich grep results with structure info (like jcode's agent grep).

  Adds: file size, symbol count, what the file exports, what imports it.
  This lets the agent infer file purpose without reading it.
  """
  if not _enabled():
    return matches

  structure = _load_structure(project_root)
  files = structure.get("files", {})
  exports = structure.get("exports", {})

  info = files.get(file_path, {})
  file_exports = exports.get(file_path, [])

  if info or file_exports:
    enrichment = {}
    if info:
      enrichment["lines"] = info.get("lines", 0)
      enrichment["symbols"] = info.get("symbols", 0)
    if file_exports:
      enrichment["exports"] = file_exports[:10]

    # Add enrichment to the first match for this file
    for m in matches:
      if m.get("path") == file_path or m.get("file") == file_path:
        m["_awareness"] = enrichment
        break

  return matches


# ── Config ───────────────────────────────────────────────────────────────────

def _enabled() -> bool:
  return os.environ.get("GEMCODE_CODEBASE_AWARENESS", "1").strip().lower() in (
    "1", "true", "yes", "on",
  )
