"""Fleet inbox (`.gemcode/fleet_reports.jsonl`) append + drain behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from gemcode.fleet_reports import (
  append_fleet_report,
  drain_for_prompt,
  has_pending_fleet_reports,
  inject_enabled,
  maybe_append_org_report,
  preview_fleet_inbox,
)
from gemcode.org import resolve_fleet_root


def test_resolve_fleet_root_from_agent_workspace(tmp_path: Path) -> None:
  fleet = tmp_path / "app"
  fleet.mkdir()
  (fleet / ".gemcode").mkdir()
  (fleet / ".gemcode" / "org.json").write_text("{}", encoding="utf-8")
  agent_ws = fleet / ".gemcode" / "agents" / "01-alpha"
  agent_ws.mkdir(parents=True)
  assert resolve_fleet_root(agent_ws).resolve() == fleet.resolve()
  assert resolve_fleet_root(fleet).resolve() == fleet.resolve()


def test_append_and_drain_clears_inbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv("GEMCODE_FLEET_REPORTS_INJECT", "1")
  assert inject_enabled()
  (tmp_path / ".gemcode").mkdir()
  (tmp_path / ".gemcode" / "org.json").write_text('{"members":[]}', encoding="utf-8")

  payload = {
    "status": "finished",
    "job_id": "job-1",
    "task": "demo",
    "member": {"name": "worker", "address": "worker"},
    "result": {"report": "done"},
  }
  maybe_append_org_report(tmp_path, payload)
  p = tmp_path / ".gemcode" / "fleet_reports.jsonl"
  assert p.is_file()
  assert has_pending_fleet_reports(tmp_path)

  drained = drain_for_prompt(tmp_path)
  assert "Fleet / agent reports" in drained
  assert "[org.report]" in drained
  assert "worker" in drained
  assert "done" in drained
  # Fully drained within budget
  assert not p.read_text(encoding="utf-8").strip()


def test_drain_respects_max_chars_and_preserves_tail(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("GEMCODE_FLEET_REPORTS_INJECT", "1")
  (tmp_path / ".gemcode").mkdir()

  # Two small finished org reports; cap low enough to fit only one formatted block.
  for i in range(2):
    maybe_append_org_report(
        tmp_path,
        {
            "status": "finished",
            "job_id": f"j{i}",
            "task": f"task-{i}",
            "member": {"name": f"m{i}"},
            "result": {"report": "ok"},
        },
    )
  p = tmp_path / ".gemcode" / "fleet_reports.jsonl"
  lines_before = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
  assert len(lines_before) == 2

  drained = drain_for_prompt(tmp_path, max_chars=100)
  assert "[org.report]" in drained
  assert "fleet_reports.jsonl" in drained
  lines_after = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
  assert len(lines_after) == 1


def test_maybe_append_org_report_ignores_non_terminal_status(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("GEMCODE_FLEET_REPORTS_INJECT", "1")
  (tmp_path / ".gemcode").mkdir()
  maybe_append_org_report(tmp_path, {"status": "delegated", "task": "x"})
  p = tmp_path / ".gemcode" / "fleet_reports.jsonl"
  assert not p.exists()


def test_inject_disabled_skips_append(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv("GEMCODE_FLEET_REPORTS_INJECT", "0")
  append_fleet_report(tmp_path, topic="org.report", payload={"status": "finished"})
  p = tmp_path / ".gemcode" / "fleet_reports.jsonl"
  assert not p.exists()


def test_preview_fleet_inbox_does_not_clear(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv("GEMCODE_FLEET_REPORTS_INJECT", "1")
  (tmp_path / ".gemcode").mkdir()
  maybe_append_org_report(
      tmp_path,
      {
          "status": "finished",
          "job_id": "j-preview",
          "task": "t",
          "member": {"name": "w"},
          "result": {"report": "hello"},
      },
  )
  p = tmp_path / ".gemcode" / "fleet_reports.jsonl"
  assert p.read_text(encoding="utf-8").strip()
  prev = preview_fleet_inbox(tmp_path)
  assert "preview" in prev.lower() or "Fleet / agent reports" in prev
  assert "hello" in prev
  assert p.read_text(encoding="utf-8").strip()
