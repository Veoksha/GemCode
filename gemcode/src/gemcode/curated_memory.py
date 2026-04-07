"""
Curated memory store (Hermes-style).

This is distinct from ADK's `.gemcode/memories.jsonl`:
- memories.jsonl: auto-generated, retrieval-oriented, noisy by design
- curated memory: small, human/agent curated facts that are safe to re-inject

Files:
  <project>/.gemcode/GEMCODE_MEMORY.md  (project facts, conventions, commands)
  <project>/.gemcode/GEMCODE_USER.md    (user preferences for this project)

Backward compatibility:
  - If older files exist, they are still read:
    - .gemcode/MEMORY.md
    - .gemcode/USER.md
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any


_SUSPICIOUS = [
  "api_key",
  "access key",
  "secret",
  "password",
  "token",
  "private key",
  "-----BEGIN",
]


def memory_paths(project_root: Path) -> tuple[Path, Path]:
  d = project_root / ".gemcode"
  return d / "GEMCODE_MEMORY.md", d / "GEMCODE_USER.md"


def _legacy_memory_paths(project_root: Path) -> tuple[Path, Path]:
  d = project_root / ".gemcode"
  return d / "MEMORY.md", d / "USER.md"


def _scan_safe(text: str) -> str | None:
  t = (text or "").strip()
  if not t:
    return "empty"
  if len(t) > 4000:
    return "too_long"
  low = t.lower()
  for s in _SUSPICIOUS:
    if s in low:
      return "looks_sensitive"
  # Block invisible control characters except newline/tab.
  for ch in t:
    o = ord(ch)
    if o < 32 and ch not in ("\n", "\t"):
      return "control_chars"
  return None


def load_snapshot(project_root: Path, *, max_chars: int = 6000) -> dict[str, Any]:
  mem, user = memory_paths(project_root)
  legacy_mem, legacy_user = _legacy_memory_paths(project_root)
  def _read(p: Path) -> str:
    if not p.is_file():
      return ""
    return p.read_text(encoding="utf-8", errors="replace")
  # Prefer new filenames; fall back to legacy if new doesn't exist.
  mem_txt = _read(mem) or _read(legacy_mem)
  user_txt = _read(user) or _read(legacy_user)
  out = (mem_txt.strip() + "\n\n" + user_txt.strip()).strip()
  if len(out) > max_chars:
    out = out[:max_chars] + "\n\n(truncated)\n"
  return {
    "exists": bool(mem_txt.strip() or user_txt.strip()),
    "memory_path": str(mem if mem.is_file() else legacy_mem),
    "user_path": str(user if user.is_file() else legacy_user),
    "text": out,
    "chars": len(out),
  }


def append_fact(project_root: Path, *, target: str, text: str) -> dict[str, Any]:
  """
  Append a curated fact to MEMORY.md or USER.md.

  target: 'memory' or 'user'
  """
  err = _scan_safe(text)
  if err:
    return {"error": f"rejected:{err}"}
  mem, user = memory_paths(project_root)
  p = mem if (target or "").strip().lower() != "user" else user
  p.parent.mkdir(parents=True, exist_ok=True)
  if not p.exists():
    hdr = "# Curated memory\n\nThis file is safe-to-inject project memory.\n\n"
    p.write_text(hdr, encoding="utf-8")
  cur = p.read_text(encoding="utf-8", errors="replace")
  stripped = text.strip()
  if stripped in cur:
    return {"status": "already_exists", "path": str(p)}
  ts = datetime.now().strftime("%Y-%m-%d %H:%M")
  entry = f"\n<!-- {ts} -->\n- {stripped}\n"
  p.write_text(cur + entry, encoding="utf-8")
  return {"status": "appended", "path": str(p)}

