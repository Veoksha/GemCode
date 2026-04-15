"""
Write-ahead log (WAL) for durable, user-visible state mutations.

Goal: auditability and safer debugging for memory-related writes without logging
full sensitive content.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def wal_path(project_root: Path) -> Path:
  return project_root / ".gemcode" / "wal.jsonl"


def _utc_now_iso() -> str:
  return datetime.now(timezone.utc).isoformat()


def _sha256_hex(text: str) -> str:
  return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


@dataclass(frozen=True)
class WalEvent:
  type: str
  ts: str
  data: dict[str, Any]


def append_wal_event(project_root: Path, *, type: str, data: dict[str, Any]) -> dict[str, Any]:
  """
  Append a JSONL WAL record under .gemcode/wal.jsonl.

  This function must be best-effort: if WAL writing fails, do not block the
  primary operation.
  """
  try:
    p = wal_path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    ev = WalEvent(type=type, ts=_utc_now_iso(), data=data)
    line = json.dumps({"type": ev.type, "ts": ev.ts, "data": ev.data}, ensure_ascii=False)
    with p.open("a", encoding="utf-8") as f:
      f.write(line + "\n")
    return {"ok": True, "path": str(p)}
  except Exception as e:
    return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def wal_text_fingerprint(text: str) -> dict[str, Any]:
  """
  Content fingerprint for WAL without storing raw text.
  """
  t = (text or "").strip()
  return {"chars": len(t), "sha256": _sha256_hex(t)}

