"""File-based HITL approval bridge for web chat (subprocess-safe).

Chat SSE runs in a ``gemcode.web.sse_adapter`` subprocess while
``POST /api/chat/approve`` is handled by the parent ``gemcode serve`` process.
In-memory waiter sets therefore cannot detect “live” approvals across that
boundary — use ``*.waiting`` marker files on the shared approval dir instead.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

def _approval_dir() -> Path:
  tenant = os.environ.get("GEMCODE_HOSTED_TENANT_ROOT", "").strip()
  if tenant:
    return Path(tenant).expanduser().resolve() / ".gemcode" / "web_approvals"
  return Path.home() / ".gemcode" / "web_approvals"
_DEFAULT_TIMEOUT_S = float(os.environ.get("GEMCODE_WEB_HITL_TIMEOUT_S", "3600"))
_POLL_INTERVAL_S = 0.25
# Same-process hints only (parent server never shares these with sse_adapter).
_active_waiters: set[str] = set()
_pending_approval_ids: set[str] = set()


def _safe_id(approval_id: str) -> str:
  return "".join(c if c.isalnum() or c in "-_:" else "_" for c in approval_id)


def _approval_path(approval_id: str) -> Path:
  return _approval_dir() / f"{_safe_id(approval_id)}.json"


def _waiting_path(approval_id: str) -> Path:
  """Marker written by the SSE subprocess while an approval is expected/active."""
  return _approval_dir() / f"{_safe_id(approval_id)}.waiting"


def _is_live_approval(approval_id: str) -> bool:
  """True if a chat turn is still waiting for this approval (cross-process safe)."""
  aid = (approval_id or "").strip()
  if not aid:
    return False
  if aid in _active_waiters or aid in _pending_approval_ids:
    return True
  try:
    path = _waiting_path(aid)
    if not path.is_file():
      return False
    # Heartbeats refresh mtime; stale markers mean the SSE subprocess died.
    age_s = time.time() - path.stat().st_mtime
    return age_s < 90.0
  except OSError:
    return False


def register_pending_approval(approval_id: str) -> None:
  """Mark an approval as expected so early UI clicks are not reported as late."""
  aid = (approval_id or "").strip()
  if not aid:
    return
  _pending_approval_ids.add(aid)
  _touch_waiting(aid)


def _touch_waiting(approval_id: str) -> None:
  try:
    _approval_dir().mkdir(parents=True, exist_ok=True)
    _waiting_path(approval_id).write_text(f"{time.time():.3f}\n", encoding="utf-8")
  except OSError:
    pass


def _clear_waiting_marker(approval_id: str) -> None:
  try:
    _waiting_path(approval_id).unlink(missing_ok=True)
  except OSError:
    pass


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
  _approval_dir().mkdir(parents=True, exist_ok=True)
  path = _approval_path(approval_id)
  # Detect live wait BEFORE writing; marker is owned by the SSE subprocess.
  live = _is_live_approval(approval_id)
  payload = {"confirmed": bool(confirmed), "resolved_ms": int(time.time() * 1000)}
  path.write_text(json.dumps(payload), encoding="utf-8")
  return {
    "ok": True,
    "approval_id": approval_id,
    "confirmed": bool(confirmed),
    # late = no waiter — UI may start a recovery turn. Never true while SSE waits.
    "late": not live,
  }


async def wait_for_web_approval(
  approval_id: str,
  *,
  timeout_s: float = _DEFAULT_TIMEOUT_S,
  heartbeat: Callable[[], None] | None = None,
  heartbeat_s: float = 15.0,
) -> bool:
  """Block until the user approves/denies or timeout (deny on timeout)."""
  _approval_dir().mkdir(parents=True, exist_ok=True)
  path = _approval_path(approval_id)
  register_pending_approval(approval_id)

  # User may have approved before we started waiting (preflight UI is immediate).
  existing = _read_approval_file(path)
  if existing is not None:
    _pending_approval_ids.discard(approval_id)
    _clear_waiting_marker(approval_id)
    return existing

  _pending_approval_ids.discard(approval_id)
  _active_waiters.add(approval_id)
  _touch_waiting(approval_id)
  try:
    deadline = time.monotonic() + max(1.0, timeout_s)
    next_hb = time.monotonic()
    while time.monotonic() < deadline:
      if path.is_file():
        result = _read_approval_file(path)
        if result is not None:
          return result
      now = time.monotonic()
      if now >= next_hb:
        _touch_waiting(approval_id)
        if heartbeat is not None:
          try:
            heartbeat()
          except Exception:
            pass
        next_hb = now + max(5.0, heartbeat_s)
      await asyncio.sleep(_POLL_INTERVAL_S)
    return False
  finally:
    _active_waiters.discard(approval_id)
    _pending_approval_ids.discard(approval_id)
    _clear_waiting_marker(approval_id)


def new_approval_id(session_id: str, fc_id: str | None) -> str:
  fid = (fc_id or "").strip() or uuid.uuid4().hex[:12]
  return f"{session_id}:{fid}"
