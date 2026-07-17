"""Tests for persistent habit run history."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gemcode.habit_runs import append_habit_run, list_habit_runs


def test_append_and_list_habit_runs(tmp_path: Path) -> None:
  append_habit_run(
    tmp_path,
    habit_name="watch",
    agent="self",
    job_id="job-1",
    status="finished",
    report="All tests passed",
    duration_ms=1200,
    ts_ms=1_000,
  )
  append_habit_run(
    tmp_path,
    habit_name="other",
    agent="self",
    job_id="job-x",
    status="finished",
    report="ignored",
    ts_ms=2_000,
  )
  runs = list_habit_runs(tmp_path, habit_name="watch", limit=10)
  assert len(runs) == 1
  assert runs[0]["job_id"] == "job-1"
  assert runs[0]["report"] == "All tests passed"


def test_list_merges_pending_fleet_inbox(tmp_path: Path) -> None:
  inbox = tmp_path / ".gemcode" / "fleet_reports.jsonl"
  inbox.parent.mkdir(parents=True, exist_ok=True)
  rec = {
    "ts_ms": 5_000,
    "topic": "job.report",
    "payload": {
      "job_id": "job-pending",
      "status": "finished",
      "member": "self",
      "report": "Fresh from inbox",
      "habit": {"name": "watch", "agent": "self"},
    },
  }
  inbox.write_text(json.dumps(rec) + "\n", encoding="utf-8")

  runs = list_habit_runs(tmp_path, habit_name="watch", limit=10)
  assert len(runs) == 1
  assert runs[0]["job_id"] == "job-pending"
  assert runs[0]["pending_inbox"] is True
