"""
Self-Healing Loop — Automatically detect and fix issues after changes.

After the agent makes changes, this module:
1. Detects what verification to run (tests, lint, typecheck, build)
2. Runs it automatically
3. If it fails, enqueues a fix job on the mesh
4. Repeats until passing or max attempts reached

This creates a closed loop: change → verify → fix → verify → done.

The agent doesn't need to be told to run tests — it happens automatically.
The agent doesn't need to be told to fix failures — it happens automatically.

Inspired by the "self-healing software" pattern where code repairs itself.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig
from gemcode.event_bus import BusMessage, get_bus


def enabled() -> bool:
  return os.environ.get("GEMCODE_SELF_HEALING", "1").strip().lower() in (
    "1", "true", "yes", "on",
  )


def max_fix_attempts() -> int:
  return int(os.environ.get("GEMCODE_SELF_HEALING_MAX_ATTEMPTS", "2"))


def _detect_verify_command(project_root: Path) -> str | None:
  """
  Detect the best verification command for this project.

  Checks for common test/lint/build configurations and returns
  the fastest meaningful check.
  """
  root = project_root

  # Python: pytest
  if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists() or (root / "setup.py").exists():
    # Check if pytest is likely available
    if (root / ".venv").exists() or (root / "venv").exists():
      return "python3 -m pytest -x -q --tb=short 2>&1 | tail -30"
    return "python3 -m pytest -x -q --tb=short 2>&1 | tail -30"

  # Node: npm test or npm run lint
  if (root / "package.json").exists():
    try:
      pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
      scripts = pkg.get("scripts", {})
      if "test" in scripts:
        return "npm test 2>&1 | tail -30"
      if "lint" in scripts:
        return "npm run lint 2>&1 | tail -30"
      if "build" in scripts:
        return "npm run build 2>&1 | tail -30"
    except Exception:
      pass
    return "npm test 2>&1 | tail -30"

  # Rust: cargo check
  if (root / "Cargo.toml").exists():
    return "cargo check 2>&1 | tail -30"

  # Go: go build
  if (root / "go.mod").exists():
    return "go build ./... 2>&1 | tail -30"

  # Makefile
  if (root / "Makefile").exists():
    return "make check 2>&1 | tail -20 || make test 2>&1 | tail -20"

  return None


def _detect_verify_command_cached(project_root: Path) -> str | None:
  """Cache the detected command in .gemcode/verify_command.txt."""
  cache = project_root / ".gemcode" / "verify_command.txt"
  if cache.is_file():
    cmd = cache.read_text(encoding="utf-8").strip()
    if cmd:
      return cmd

  cmd = _detect_verify_command(project_root)
  if cmd:
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(cmd + "\n", encoding="utf-8")
  return cmd


class SelfHealingLoop:
  """
  Subscribes to checkpoint.created events and auto-verifies + auto-fixes.

  Flow:
  1. Files changed (checkpoint.created event)
  2. Run verification command
  3. If passes → done
  4. If fails → enqueue fix job on mesh with error output
  5. After fix → re-verify (up to max_fix_attempts)
  """

  def __init__(self, cfg: GemCodeConfig) -> None:
    self.cfg = cfg
    self._bus = get_bus()
    self._active = enabled()
    self._fix_attempts: dict[str, int] = {}  # checkpoint_id → attempts
    self._last_verify_ms: float = 0
    self._cooldown_s = 30.0  # Don't verify more than once per 30s

    if self._active:
      self._bus.subscribe(topic="checkpoint.created", callback=self._on_checkpoint)
      self._bus.subscribe(topic="job.report", callback=self._on_fix_result)

  async def _on_checkpoint(self, msg: BusMessage) -> None:
    """Triggered when files are modified. Run verification."""
    if not self._active:
      return

    # Cooldown: don't spam verification
    now = time.time()
    if (now - self._last_verify_ms) < self._cooldown_s:
      return
    self._last_verify_ms = now

    # Detect verification command
    cmd = _detect_verify_command_cached(self.cfg.project_root)
    if not cmd:
      return

    checkpoint_id = msg.payload.get("checkpoint_id", "")
    files = msg.payload.get("files", [])

    # Run verification via the mesh
    try:
      from gemcode.agent_mesh import get_mesh
      mesh = get_mesh(self.cfg)
      if mesh is None:
        return

      mesh.enqueue(
        prompt=(
          f"Run this verification command and report the result:\n"
          f"```bash\n{cmd}\n```\n\n"
          f"Files that were just modified: {', '.join(files[:10])}\n\n"
          "Rules:\n"
          "- Run the command exactly as shown.\n"
          "- If it passes (exit code 0, all tests pass), report: PASS\n"
          "- If it fails, report the EXACT error output and which file/line failed.\n"
          "- Do NOT attempt to fix anything — just report the result.\n"
          "- Return JSON: {\"status\": \"pass|fail\", \"output\": \"...\", \"failed_tests\": [...]}\n"
        ),
        priority=4,  # High priority — verification should run quickly
        member_name="kaira",
        meta={
          "self_healing": True,
          "phase": "verify",
          "checkpoint_id": checkpoint_id,
          "verify_command": cmd,
          "files": files[:20],
        },
      )
    except Exception:
      pass

  async def _on_fix_result(self, msg: BusMessage) -> None:
    """
    After a verification or fix job completes, check if we need to auto-fix.
    """
    if not self._active:
      return

    payload = msg.payload
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}

    # Only process self-healing jobs
    if not isinstance(meta, dict):
      # Try to detect from the report content
      report = str(payload.get("report") or "")
      if "self_healing" not in report and "PASS" not in report and "FAIL" not in report:
        return

    status = str(payload.get("status") or "")
    if status != "finished":
      return

    report = str(payload.get("report") or "")

    # If verification passed, we're done
    if "PASS" in report.upper() and "FAIL" not in report.upper():
      await self._bus.publish(BusMessage(
        topic="self_healing.pass",
        from_addr="self_healing",
        payload={"message": "All checks passing after changes."},
      ))
      return

    # Verification failed — should we auto-fix?
    checkpoint_id = meta.get("checkpoint_id", "unknown")
    attempts = self._fix_attempts.get(checkpoint_id, 0)

    if attempts >= max_fix_attempts():
      # Give up — publish failure event
      await self._bus.publish(BusMessage(
        topic="self_healing.failed",
        from_addr="self_healing",
        payload={
          "message": f"Self-healing gave up after {attempts} fix attempts.",
          "last_error": report[:2000],
        },
      ))
      return

    # Enqueue a fix job
    self._fix_attempts[checkpoint_id] = attempts + 1

    try:
      from gemcode.agent_mesh import get_mesh
      mesh = get_mesh(self.cfg)
      if mesh is None:
        return

      cmd = meta.get("verify_command") or _detect_verify_command_cached(self.cfg.project_root) or ""
      files = meta.get("files", [])

      mesh.enqueue(
        prompt=(
          f"The verification command failed after recent changes.\n\n"
          f"Verification command: `{cmd}`\n"
          f"Files recently modified: {', '.join(files[:10])}\n\n"
          f"Error output:\n```\n{report[:3000]}\n```\n\n"
          "Fix the issue:\n"
          "1. Read the failing file(s)\n"
          "2. Identify the root cause from the error\n"
          "3. Apply the minimal fix\n"
          "4. Run the verification command again to confirm\n"
          "5. Report what you fixed\n"
        ),
        priority=5,  # Higher priority than verification
        member_name="kaira",
        meta={
          "self_healing": True,
          "phase": "fix",
          "checkpoint_id": checkpoint_id,
          "verify_command": cmd,
          "files": files,
          "attempt": attempts + 1,
        },
      )
    except Exception:
      pass
