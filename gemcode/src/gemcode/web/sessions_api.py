"""Web API helpers for ADK session list / naming (CLI /session parity)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def handle_sessions_get(project_root: str) -> tuple[int, dict[str, Any]]:
  root = Path(project_root).expanduser().resolve()
  if not root.is_dir():
    return 400, {"ok": False, "error": "Invalid project root"}
  try:
    from gemcode.session_store import list_sessions

    sessions = list_sessions(root, max_items=50)
    return 200, {"ok": True, "sessions": sessions, "project_root": str(root)}
  except Exception as exc:
    return 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def handle_sessions_post(project_root: str, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
  root = Path(project_root).expanduser().resolve()
  if not root.is_dir():
    return 400, {"ok": False, "error": "Invalid project root"}

  action = str(data.get("action") or "").strip().lower()
  session_id = str(data.get("session_id") or "").strip()
  if not session_id:
    return 400, {"ok": False, "error": "session_id is required"}

  try:
    from gemcode.session_store import name_session, touch_session

    if action in ("name", "rename"):
      name = str(data.get("name") or "").strip()
      if not name:
        return 400, {"ok": False, "error": "name is required"}
      name_session(root, session_id, name)
      return 200, {"ok": True, "session_id": session_id, "name": name}
    if action == "touch":
      touch_session(root, session_id)
      return 200, {"ok": True, "session_id": session_id}
    return 400, {"ok": False, "error": f"Unknown action: {action}"}
  except Exception as exc:
    return 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
