"""Persistent audit log for scheduled habit (mesh job) completions."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _habit_runs_path(fleet_root: Path) -> Path:
  return fleet_root / ".gemcode" / "habit_runs.jsonl"


def append_habit_run(
  fleet_root: Path,
  *,
  habit_name: str,
  agent: str = "",
  job_id: str = "",
  status: str = "",
  report: str = "",
  error: str = "",
  duration_ms: int | None = None,
  session_id: str = "",
  ts_ms: int | None = None,
) -> None:
  """Append one habit run record (never drained — unlike fleet_reports.jsonl)."""
  nm = (habit_name or "").strip().lower()
  if not nm:
    return
  try:
    p = _habit_runs_path(fleet_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec: dict[str, Any] = {
      "ts_ms": int(ts_ms or int(time.time() * 1000)),
      "habit_name": nm,
      "agent": (agent or "").strip(),
      "job_id": (job_id or "").strip(),
      "status": (status or "").strip().lower() or "unknown",
      "report": (report or "").strip()[:12_000],
      "error": (error or "").strip()[:4000],
      "session_id": (session_id or "").strip(),
    }
    if duration_ms is not None:
      rec["duration_ms"] = int(duration_ms)
    line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
    with open(p, "a", encoding="utf-8") as f:
      f.write(line)
      f.flush()
  except OSError:
    pass


def _habit_name_from_payload(payload: dict[str, Any]) -> str:
  hab = payload.get("habit")
  if isinstance(hab, dict):
    return str(hab.get("name") or "").strip().lower()
  return ""


def _record_from_job_report(rec: dict[str, Any]) -> dict[str, Any] | None:
  if str(rec.get("topic") or "") != "job.report":
    return None
  payload = rec.get("payload")
  if not isinstance(payload, dict):
    return None
  habit_name = _habit_name_from_payload(payload)
  if not habit_name:
    return None
  hab = payload.get("habit") if isinstance(payload.get("habit"), dict) else {}
  return {
    "ts_ms": int(rec.get("ts_ms") or int(time.time() * 1000)),
    "habit_name": habit_name,
    "agent": str(hab.get("agent") or payload.get("member") or "").strip(),
    "job_id": str(payload.get("job_id") or "").strip(),
    "status": str(payload.get("status") or "").strip().lower() or "unknown",
    "report": str(payload.get("report") or "").strip()[:12_000],
    "error": str(payload.get("error") or "").strip()[:4000],
    "session_id": str(payload.get("session_id") or "").strip(),
    "duration_ms": payload.get("duration_ms"),
    "pending_inbox": True,
  }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
  if not path.is_file():
    return []
  try:
    raw = path.read_text(encoding="utf-8", errors="replace")
  except OSError:
    return []
  out: list[dict[str, Any]] = []
  for line in raw.splitlines():
    line = line.strip()
    if not line:
      continue
    try:
      rec = json.loads(line)
    except json.JSONDecodeError:
      continue
    if isinstance(rec, dict):
      out.append(rec)
  return out


def list_habit_runs(
  fleet_root: Path,
  *,
  habit_name: str,
  limit: int = 50,
) -> list[dict[str, Any]]:
  """Return recent runs for one habit, newest first. Merges audit log + pending inbox."""
  nm = (habit_name or "").strip().lower()
  if not nm:
    return []

  limit = max(1, min(200, int(limit)))
  by_job: dict[str, dict[str, Any]] = {}

  for rec in _read_jsonl(_habit_runs_path(fleet_root)):
    if str(rec.get("habit_name") or "").strip().lower() != nm:
      continue
    jid = str(rec.get("job_id") or "").strip() or f"run-{rec.get('ts_ms')}"
    by_job[jid] = {k: v for k, v in rec.items() if k != "pending_inbox"}

  inbox_path = fleet_root / ".gemcode" / "fleet_reports.jsonl"
  for rec in _read_jsonl(inbox_path):
    row = _record_from_job_report(rec)
    if row is None or row["habit_name"] != nm:
      continue
    jid = row["job_id"] or f"pending-{row['ts_ms']}"
    if jid not in by_job:
      by_job[jid] = row

  runs = sorted(by_job.values(), key=lambda r: int(r.get("ts_ms") or 0), reverse=True)
  return runs[:limit]
