"""
Session store — named session management on top of ADK's SqliteSessionService.

Provides:
  - list_sessions()     — list all sessions for the current project
  - name_session()      — give a human name to a session ID
  - get_session_name()  — look up a session name by ID
  - find_session()      — look up a session ID by name or prefix

Metadata is stored in a lightweight `sessions_meta.json` alongside sessions.sqlite.
Each session entry: { "id": "...", "name": "...", "created_at": "...", "last_used": "..." }
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def _meta_path(project_root: Path) -> Path:
  return project_root / ".gemcode" / "sessions_meta.json"


def _load_meta(project_root: Path) -> dict[str, dict]:
  """Load {session_id: {name, created_at, last_used}} from sessions_meta.json."""
  p = _meta_path(project_root)
  if not p.is_file():
    return {}
  try:
    return json.loads(p.read_text(encoding="utf-8"))
  except Exception:
    return {}


def _save_meta(project_root: Path, meta: dict[str, dict]) -> None:
  p = _meta_path(project_root)
  p.parent.mkdir(parents=True, exist_ok=True)
  p.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_sqlite_sessions(db_path: Path) -> list[dict]:
  """Read raw session rows from ADK's SQLite db."""
  if not db_path.is_file():
    return []
  try:
    con = sqlite3.connect(str(db_path), timeout=5)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    # ADK stores sessions in a "sessions" table with at least (id, app_name, user_id, create_time, update_time)
    cur.execute("""
      SELECT id,
             COALESCE(create_time, '') as create_time,
             COALESCE(update_time, '') as update_time
      FROM sessions
      WHERE app_name='gemcode' AND user_id='local'
      ORDER BY update_time DESC
      LIMIT 200
    """)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows
  except Exception:
    return []


def _count_events(db_path: Path, session_id: str) -> int:
  """Count the number of events (messages) in a session."""
  if not db_path.is_file():
    return 0
  try:
    con = sqlite3.connect(str(db_path), timeout=5)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM events WHERE session_id=?", (session_id,))
    count = (cur.fetchone() or (0,))[0]
    con.close()
    return count
  except Exception:
    return 0


def list_sessions(project_root: Path, *, max_items: int = 20) -> list[dict[str, Any]]:
  """
  Return a list of sessions, most-recently-used first.

  Each entry:
    id         : str    — full session UUID
    name       : str    — human name (or "" if unnamed)
    created_at : str    — ISO timestamp or ""
    last_used  : str    — ISO timestamp or ""
    events     : int    — approximate turn count
    short_id   : str    — first 8 chars of id
  """
  from gemcode.session_runtime import session_db_path
  db = session_db_path(project_root)
  rows = _read_sqlite_sessions(db)
  meta = _load_meta(project_root)

  sessions = []
  for row in rows[:max_items]:
    sid = row["id"]
    entry = meta.get(sid, {})
    sessions.append({
      "id": sid,
      "short_id": sid[:8] if len(sid) >= 8 else sid,
      "name": entry.get("name", ""),
      "created_at": entry.get("created_at", row.get("create_time", "")),
      "last_used": entry.get("last_used", row.get("update_time", "")),
      "events": _count_events(db, sid),
    })
  return sessions


def name_session(project_root: Path, session_id: str, name: str) -> None:
  """Assign a human name to a session."""
  meta = _load_meta(project_root)
  entry = meta.get(session_id, {})
  entry["name"] = name.strip()
  if "created_at" not in entry:
    entry["created_at"] = datetime.now().isoformat(timespec="seconds")
  entry["last_used"] = datetime.now().isoformat(timespec="seconds")
  meta[session_id] = entry
  _save_meta(project_root, meta)


def touch_session(project_root: Path, session_id: str) -> None:
  """Update last_used timestamp for a session (called at session start/each turn)."""
  meta = _load_meta(project_root)
  entry = meta.get(session_id, {})
  now = datetime.now().isoformat(timespec="seconds")
  entry.setdefault("created_at", now)
  entry["last_used"] = now
  meta[session_id] = entry
  _save_meta(project_root, meta)


def get_session_name(project_root: Path, session_id: str) -> str:
  """Return the human name for a session, or "" if unnamed."""
  meta = _load_meta(project_root)
  return meta.get(session_id, {}).get("name", "")


def find_session(project_root: Path, query: str) -> str | None:
  """
  Find a session ID by:
    1. Exact UUID match
    2. ID prefix match (first N characters)
    3. Name substring match (case-insensitive)

  Returns the full session ID or None if not found.
  """
  sessions = list_sessions(project_root, max_items=200)
  q = query.strip().lower()

  # Exact ID match
  for s in sessions:
    if s["id"] == query:
      return s["id"]

  # ID prefix match
  for s in sessions:
    if s["id"].lower().startswith(q):
      return s["id"]

  # Name match
  for s in sessions:
    if s["name"] and q in s["name"].lower():
      return s["id"]

  return None


def format_session_list(sessions: list[dict[str, Any]]) -> list[str]:
  """Format sessions for /session list display."""
  if not sessions:
    return ["  (no sessions yet)"]
  lines = []
  for i, s in enumerate(sessions):
    marker = "→" if i == 0 else " "
    name_part = f"  [{s['name']}]" if s["name"] else ""
    turns = f"  {s['events']} turns" if s["events"] > 0 else ""
    # Format date nicely
    last = s.get("last_used", "")
    if last and "T" in last:
      try:
        dt = datetime.fromisoformat(last)
        last = dt.strftime("%b %d %H:%M")
      except Exception:
        last = last[:16]
    date_part = f"  {last}" if last else ""
    lines.append(f"  {marker} {s['short_id']}{name_part}{date_part}{turns}")
  return lines
