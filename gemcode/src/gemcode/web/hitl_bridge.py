"""File-based HITL approval bridge for web chat (subprocess-safe)."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

_APPROVAL_DIR = Path.home() / ".gemcode" / "web_approvals"
_DEFAULT_TIMEOUT_S = 300.0
_POLL_INTERVAL_S = 0.25
# approval_ids currently blocked in wait_for_web_approval (same process)
_active_waiters: set[str] = set()


def _approval_path(approval_id: str) -> Path:
  safe = "".join(c if c.isalnum() or c in "-_:" else "_" for c in approval_id)
  return _APPROVAL_DIR / f"{safe}.json"


def _read_approval_file(path: Path) -> bool | None:
  """Return confirmed bool if resolved, else None."""
  if not path.is_file():
    return None
  try:
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
      path.unlink()
    except OSError:
      pass
    return bool(data.get("confirmed"))
  except (json.JSONDecodeError, OSError):
    try:
      path.unlink()
    except OSError:
      pass
    return False


def resolve_web_approval(approval_id: str, *, confirmed: bool) -> dict[str, Any]:
  """Record the user's approve/deny decision (called from HTTP handler)."""
  if not approval_id.strip():
    return {"ok": False, "error": "approval_id is required"}
  _APPROVAL_DIR.mkdir(parents=True, exist_ok=True)
  path = _approval_path(approval_id)
  payload = {"confirmed": bool(confirmed), "resolved_ms": int(time.time() * 1000)}
  path.write_text(json.dumps(payload), encoding="utf-8")
  late = approval_id not in _active_waiters
  return {
    "ok": True,
    "approval_id": approval_id,
    "confirmed": bool(confirmed),
    "late": late,
  }


async def wait_for_web_approval(
  approval_id: str,
  *,
  timeout_s: float = _DEFAULT_TIMEOUT_S,
  heartbeat: Callable[[], None] | None = None,
  heartbeat_s: float = 15.0,
) -> bool:
  """Block until the user approves/denies or timeout (deny on timeout)."""
  _APPROVAL_DIR.mkdir(parents=True, exist_ok=True)
  path = _approval_path(approval_id)

  # User may have approved before we started waiting (preflight UI is immediate).
  existing = _read_approval_file(path)
  if existing is not None:
    return existing

  _active_waiters.add(approval_id)
  try:
    deadline = time.monotonic() + max(1.0, timeout_s)
    next_hb = time.monotonic()
    while time.monotonic() < deadline:
      if path.is_file():
        result = _read_approval_file(path)
        if result is not None:
          return result
      now = time.monotonic()
      if heartbeat is not None and now >= next_hb:
        try:
          heartbeat()
        except Exception:
          pass
        next_hb = now + max(5.0, heartbeat_s)
      await asyncio.sleep(_POLL_INTERVAL_S)
    return False
  finally:
    _active_waiters.discard(approval_id)


def new_approval_id(session_id: str, fc_id: str | None) -> str:
  fid = (fc_id or "").strip() or uuid.uuid4().hex[:12]
  return f"{session_id}:{fid}"
