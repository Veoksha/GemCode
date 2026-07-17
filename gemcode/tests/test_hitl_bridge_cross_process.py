"""Cross-process HITL late detection (parent approve vs sse_adapter wait)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from gemcode.web import hitl_bridge


def test_resolve_not_late_when_waiting_marker_exists(tmp_path: Path, monkeypatch) -> None:
  monkeypatch.setenv("GEMCODE_HOSTED_TENANT_ROOT", str(tmp_path))
  aid = "sess:tool1"
  hitl_bridge.register_pending_approval(aid)
  assert (tmp_path / ".gemcode" / "web_approvals" / "sess:tool1.waiting").is_file()

  # Simulate parent process: clear in-memory sets (as if another process).
  hitl_bridge._pending_approval_ids.clear()
  hitl_bridge._active_waiters.clear()

  out = hitl_bridge.resolve_web_approval(aid, confirmed=True)
  assert out["ok"] is True
  assert out["late"] is False


def test_resolve_late_when_no_waiter(tmp_path: Path, monkeypatch) -> None:
  monkeypatch.setenv("GEMCODE_HOSTED_TENANT_ROOT", str(tmp_path))
  hitl_bridge._pending_approval_ids.clear()
  hitl_bridge._active_waiters.clear()
  out = hitl_bridge.resolve_web_approval("sess:orphan", confirmed=True)
  assert out["ok"] is True
  assert out["late"] is True


def test_wait_picks_up_parent_resolve(tmp_path: Path, monkeypatch) -> None:
  monkeypatch.setenv("GEMCODE_HOSTED_TENANT_ROOT", str(tmp_path))
  aid = "sess:bash1"

  async def _run() -> bool:
    async def approve_soon() -> None:
      await asyncio.sleep(0.35)
      # Parent server process has empty in-memory waiter sets.
      hitl_bridge._pending_approval_ids.clear()
      hitl_bridge._active_waiters.clear()
      out = hitl_bridge.resolve_web_approval(aid, confirmed=True)
      assert out["late"] is False  # *.waiting marker from waiter process

    task = asyncio.create_task(approve_soon())
    confirmed = await hitl_bridge.wait_for_web_approval(aid, timeout_s=5.0)
    await task
    return confirmed

  assert asyncio.run(_run()) is True
