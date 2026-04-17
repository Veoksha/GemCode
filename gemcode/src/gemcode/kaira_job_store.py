from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Status = Literal["queued", "running", "finished", "failed", "cancelled"]


def _now_ms() -> int:
  return int(time.time() * 1000)


def _jobs_dir(project_root: Path) -> Path:
  return project_root / ".gemcode" / "kaira"


def _job_path(project_root: Path, job_id: str) -> Path:
  # One JSON per job, atomic replace on update.
  return _jobs_dir(project_root) / "jobs" / f"{job_id}.json"


@dataclass
class JobRecord:
  job_id: str
  session_id: str
  priority: int
  prompt: str
  status: Status
  created_ms: int
  updated_ms: int
  started_ms: int | None = None
  finished_ms: int | None = None
  error: str | None = None
  last_text: str = ""

  def to_dict(self) -> dict[str, Any]:
    return {
      "job_id": self.job_id,
      "session_id": self.session_id,
      "priority": int(self.priority),
      "prompt": self.prompt,
      "status": self.status,
      "created_ms": int(self.created_ms),
      "updated_ms": int(self.updated_ms),
      "started_ms": self.started_ms,
      "finished_ms": self.finished_ms,
      "error": self.error,
      "last_text": self.last_text,
    }


class KairaJobStore:
  """Simple persisted job registry under `.gemcode/kaira/jobs/` (JSON per job).

  This is intentionally low-dependency and robust across platforms.
  """

  def __init__(self, *, project_root: Path) -> None:
    self.project_root = Path(project_root)

  def init(self) -> None:
    p = _jobs_dir(self.project_root) / "jobs"
    p.mkdir(parents=True, exist_ok=True)

  def upsert(self, rec: JobRecord) -> None:
    self.init()
    p = _job_path(self.project_root, rec.job_id)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)

  def load(self, job_id: str) -> JobRecord | None:
    p = _job_path(self.project_root, job_id)
    if not p.exists():
      return None
    try:
      obj = json.loads(p.read_text(encoding="utf-8"))
      return JobRecord(
        job_id=str(obj.get("job_id") or job_id),
        session_id=str(obj.get("session_id") or ""),
        priority=int(obj.get("priority") or 0),
        prompt=str(obj.get("prompt") or ""),
        status=str(obj.get("status") or "queued"),  # type: ignore[arg-type]
        created_ms=int(obj.get("created_ms") or 0),
        updated_ms=int(obj.get("updated_ms") or 0),
        started_ms=obj.get("started_ms"),
        finished_ms=obj.get("finished_ms"),
        error=obj.get("error"),
        last_text=str(obj.get("last_text") or ""),
      )
    except Exception:
      return None

  def list(self, *, limit: int = 200) -> list[JobRecord]:
    base = _jobs_dir(self.project_root) / "jobs"
    if not base.exists():
      return []
    out: list[JobRecord] = []
    try:
      paths = sorted(base.glob("job_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
      paths = list(base.glob("job_*.json"))
    for p in paths[: max(1, int(limit))]:
      rec = self.load(p.stem)
      if rec is not None:
        out.append(rec)
    return out


def new_job_record(*, job_id: str, session_id: str, priority: int, prompt: str) -> JobRecord:
  now = _now_ms()
  return JobRecord(
    job_id=job_id,
    session_id=session_id,
    priority=int(priority),
    prompt=prompt,
    status="queued",
    created_ms=now,
    updated_ms=now,
  )


def mark_running(rec: JobRecord) -> JobRecord:
  now = _now_ms()
  rec.status = "running"
  rec.started_ms = rec.started_ms or now
  rec.updated_ms = now
  return rec


def mark_finished(rec: JobRecord) -> JobRecord:
  now = _now_ms()
  rec.status = "finished"
  rec.finished_ms = now
  rec.updated_ms = now
  return rec


def mark_failed(rec: JobRecord, error: str) -> JobRecord:
  now = _now_ms()
  rec.status = "failed"
  rec.error = error
  rec.finished_ms = now
  rec.updated_ms = now
  return rec


def mark_cancelled(rec: JobRecord) -> JobRecord:
  now = _now_ms()
  rec.status = "cancelled"
  rec.finished_ms = now
  rec.updated_ms = now
  return rec

