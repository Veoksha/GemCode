"""
Agent Habits — Autonomous scheduled behaviors for agents.

Habits are recurring tasks that agents perform on a schedule without user
intervention. They run inside the Agent Mesh (no separate daemon needed).

Examples:
- "Every 30 minutes, check if tests still pass"
- "Every 2 hours, summarize what changed in the repo"
- "Nightly at 2am, run a full security audit"
- "Every 5 minutes, check for new issues in the tracker"

Habits are stored in `.gemcode/habits.json` and can be managed via tools
or the REPL. Each habit specifies:
- Which agent runs it (org member)
- What they do (prompt)
- When they do it (interval, cron, or daily)
- Whether they're enabled

The HabitScheduler runs as a background asyncio task inside the mesh,
polling habits and enqueuing work when due.

Usage:
  # From the agent (tools):
  habits_add(name="test-watch", agent="kaira", prompt="Run pytest -q", every_minutes=30)
  habits_add(name="nightly-audit", agent="verifier", prompt="Full security review", daily_at="02:00")
  habits_list()
  habits_remove("test-watch")
  habits_pause("nightly-audit")
  habits_resume("nightly-audit")

  # From the filesystem:
  # .gemcode/habits.json
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig


@dataclass
class Habit:
  """A recurring scheduled behavior for an agent."""
  name: str
  agent: str  # org member name
  prompt: str  # what to do
  enabled: bool = True
  # Schedule (one of these should be set)
  every_seconds: int | None = None  # interval in seconds
  cron: str | None = None  # "M H * * *" (minute, hour)
  daily_at: str | None = None  # "HH:MM"
  # Metadata
  priority: int = 0
  max_runs: int | None = None  # None = unlimited
  run_count: int = 0
  last_run_ms: int = 0
  created_ms: int = field(default_factory=lambda: int(time.time() * 1000))

  def to_dict(self) -> dict[str, Any]:
    return {
      "name": self.name,
      "agent": self.agent,
      "prompt": self.prompt,
      "enabled": self.enabled,
      "every_seconds": self.every_seconds,
      "cron": self.cron,
      "daily_at": self.daily_at,
      "priority": self.priority,
      "max_runs": self.max_runs,
      "run_count": self.run_count,
      "last_run_ms": self.last_run_ms,
      "created_ms": self.created_ms,
    }


def _habits_path(project_root: Path) -> Path:
  return project_root / ".gemcode" / "habits.json"


def load_habits(project_root: Path) -> list[Habit]:
  """Load habits from .gemcode/habits.json."""
  p = _habits_path(project_root)
  if not p.is_file():
    return []
  try:
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
      return []
    raw = data.get("habits", [])
    if not isinstance(raw, list):
      return []
    out: list[Habit] = []
    for item in raw:
      if not isinstance(item, dict):
        continue
      name = str(item.get("name") or "").strip()
      agent = str(item.get("agent") or "").strip()
      prompt = str(item.get("prompt") or "").strip()
      if not name or not agent or not prompt:
        continue
      out.append(Habit(
        name=name,
        agent=agent,
        prompt=prompt,
        enabled=bool(item.get("enabled", True)),
        every_seconds=item.get("every_seconds"),
        cron=item.get("cron"),
        daily_at=item.get("daily_at"),
        priority=int(item.get("priority") or 0),
        max_runs=item.get("max_runs"),
        run_count=int(item.get("run_count") or 0),
        last_run_ms=int(item.get("last_run_ms") or 0),
        created_ms=int(item.get("created_ms") or 0),
      ))
    return out
  except Exception:
    return []


def save_habits(project_root: Path, habits: list[Habit]) -> None:
  """Save habits to .gemcode/habits.json."""
  p = _habits_path(project_root)
  p.parent.mkdir(parents=True, exist_ok=True)
  data = {"habits": [h.to_dict() for h in habits]}
  p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _is_due(habit: Habit, now_s: float) -> bool:
  """Check if a habit is due to run."""
  if not habit.enabled:
    return False
  if habit.max_runs is not None and habit.run_count >= habit.max_runs:
    return False

  last_s = habit.last_run_ms / 1000.0 if habit.last_run_ms else 0.0

  # Interval-based
  if habit.every_seconds and habit.every_seconds > 0:
    if last_s == 0:
      return True
    return (now_s - last_s) >= float(habit.every_seconds)

  # Daily at HH:MM
  if habit.daily_at:
    try:
      hh, mm = habit.daily_at.split(":", 1)
      h, m = int(hh), int(mm)
      lt = time.localtime(now_s)
      fire_today = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, lt.tm_wday, lt.tm_yday, lt.tm_isdst))
      if last_s == 0:
        return now_s >= fire_today
      return last_s < fire_today <= now_s
    except Exception:
      return False

  # Cron "M H * * *"
  if habit.cron:
    return _cron_due(now_s=now_s, last_s=last_s or None, cron=habit.cron)

  return False


def _cron_due(*, now_s: float, last_s: float | None, cron: str) -> bool:
  """Minimal cron: "M H * * *" with *, */N, or integer."""
  parts = (cron or "").split()
  if len(parts) != 5:
    return False
  m_s, h_s, dom, mon, dow = parts
  if dom != "*" or mon != "*" or dow != "*":
    return False

  def _match(field_str: str, val: int, *, min_v: int, max_v: int) -> bool:
    if field_str == "*":
      return True
    if field_str.startswith("*/"):
      try:
        step = int(field_str[2:])
        return step > 0 and (val - min_v) % step == 0
      except Exception:
        return False
    try:
      return int(field_str) == val and min_v <= int(field_str) <= max_v
    except Exception:
      return False

  lt = time.localtime(now_s)
  if not (_match(m_s, lt.tm_min, min_v=0, max_v=59) and _match(h_s, lt.tm_hour, min_v=0, max_v=23)):
    return False
  minute_start = now_s - float(lt.tm_sec)
  if last_s is None:
    return True
  return last_s < minute_start <= now_s


class HabitScheduler:
  """
  Background scheduler that polls habits and enqueues work on the mesh.

  Runs as an asyncio task inside the mesh process. Checks every 10 seconds
  for due habits and enqueues them as mesh jobs.
  """

  def __init__(self, cfg: GemCodeConfig) -> None:
    self.cfg = cfg
    self._task: asyncio.Task | None = None
    self._stop = asyncio.Event()
    self._poll_interval = float(os.environ.get("GEMCODE_HABITS_POLL_S", "10"))

  def start(self) -> None:
    """Start the habit scheduler loop."""
    if self._task is None or self._task.done():
      self._task = asyncio.create_task(self._loop())

  def stop(self) -> None:
    """Stop the scheduler."""
    self._stop.set()
    if self._task and not self._task.done():
      self._task.cancel()

  async def _loop(self) -> None:
    """Poll habits and enqueue due ones."""
    while not self._stop.is_set():
      try:
        await self._check_and_fire()
      except Exception:
        pass
      await asyncio.sleep(self._poll_interval)

  async def _check_and_fire(self) -> None:
    """Check all habits and fire any that are due."""
    if not _enabled():
      return

    habits = load_habits(self.cfg.project_root)
    if not habits:
      return

    now_s = time.time()
    changed = False

    for habit in habits:
      if not _is_due(habit, now_s):
        continue

      # Fire the habit
      try:
        from gemcode.agent_mesh import get_mesh
        mesh = get_mesh(self.cfg)
        if mesh is None:
          continue

        mesh.enqueue(
          prompt=habit.prompt,
          priority=habit.priority,
          member_name=habit.agent,
          meta={"habit": {"name": habit.name, "agent": habit.agent}},
        )

        # Update state
        habit.last_run_ms = int(now_s * 1000)
        habit.run_count += 1
        changed = True

        # Publish event
        from gemcode.event_bus import BusMessage, get_bus
        bus = get_bus()
        await bus.publish(BusMessage(
          topic="habit.fired",
          from_addr="scheduler",
          payload={"name": habit.name, "agent": habit.agent, "run_count": habit.run_count},
        ))
      except Exception:
        pass

    if changed:
      save_habits(self.cfg.project_root, habits)


def _enabled() -> bool:
  return os.environ.get("GEMCODE_AGENT_HABITS", "1").strip().lower() in (
    "1", "true", "yes", "on",
  )


# ── Tools ──────────────────────────────────────────────────────────────────

def make_habits_tools(cfg: GemCodeConfig) -> list:
  """Build tools for managing agent habits."""

  def habits_list() -> dict:
    """List all configured agent habits (scheduled recurring tasks)."""
    habits = load_habits(cfg.project_root)
    return {
      "ok": True,
      "habits": [h.to_dict() for h in habits],
      "count": len(habits),
    }

  def habits_add(
    name: str,
    agent: str,
    prompt: str,
    every_minutes: int = 0,
    every_seconds: int = 0,
    daily_at: str = "",
    cron: str = "",
    priority: int = 0,
    max_runs: int = 0,
  ) -> dict:
    """
    Add a recurring habit for an agent.

    A habit is a scheduled task that runs automatically on a timer.
    The agent wakes up, does the task, reports back, then sleeps until next time.
    Habits run inside the main GemCode process — no separate daemon needed.
    They fire as long as GemCode is open (REPL/TUI session).

    Results go to the fleet inbox (.gemcode/fleet_reports.jsonl). Fleet auto-continue
    (GEMCODE_FLEET_REPORTS_AUTO_CONTINUE, default on) runs digest turns **after each assistant
    reply** when the inbox still has entries — it does **not** wake the model while the TUI is
    idle at the prompt. While waiting at ❯, use **`/fleet`** (digest) or **`/fleet show`** (peek),
    or send any message; the TUI also prints a throttled hint when mesh jobs finish
    (GEMCODE_FLEET_TUI_NOTIFY).

    Args:
      name: Unique name for this habit (e.g., "test-watch", "nightly-audit").
      agent: Org member name to run this (e.g., "kaira", "verifier").
             Use "self" or "main" to run as the main GemCode agent.
      prompt: What the agent should do each time it wakes up.
      every_minutes: Run every N minutes (e.g., 30 = every half hour).
      every_seconds: Run every N seconds (for fine-grained intervals).
      daily_at: Run once daily at this time (e.g., "02:00", "14:30").
      cron: Cron expression "M H * * *" (e.g., "0 */2 * * *" = every 2 hours).
      priority: Job priority (higher = runs first when queue is busy).
      max_runs: Stop after this many runs (None = unlimited).

    Examples:
      habits_add("test-watch", "kaira", "Run pytest -q and report", every_minutes=30)
      habits_add("nightly-audit", "verifier", "Full security review", daily_at="02:00")
      habits_add("hourly-status", "self", "Summarize what changed in the last hour", cron="0 * * * *")
    """
    import re
    nm = (name or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", nm):
      return {"ok": False, "error": "invalid name (lowercase, numbers, dashes, max 64 chars)"}
    if not agent.strip():
      return {"ok": False, "error": "agent is required"}
    if not prompt.strip():
      return {"ok": False, "error": "prompt is required"}

    # Determine schedule
    secs = None
    if every_minutes and every_minutes > 0:
      secs = int(every_minutes) * 60
    elif every_seconds and every_seconds > 0:
      secs = int(every_seconds)

    if not secs and not daily_at.strip() and not cron.strip():
      return {"ok": False, "error": "must specify one of: every_minutes, every_seconds, daily_at, or cron"}

    habits = load_habits(cfg.project_root)
    # Remove existing with same name
    habits = [h for h in habits if h.name != nm]
    habits.append(Habit(
      name=nm,
      agent=agent.strip(),
      prompt=prompt.strip(),
      enabled=True,
      every_seconds=secs,
      daily_at=daily_at.strip() or None,
      cron=cron.strip() or None,
      priority=priority,
      max_runs=max_runs if max_runs > 0 else None,
    ))
    save_habits(cfg.project_root, habits)
    return {"ok": True, "name": nm, "agent": agent, "schedule": daily_at or cron or f"every {secs}s"}

  def habits_remove(name: str) -> dict:
    """Remove a habit by name."""
    habits = load_habits(cfg.project_root)
    before = len(habits)
    habits = [h for h in habits if h.name != name.strip().lower()]
    save_habits(cfg.project_root, habits)
    return {"ok": True, "removed": before - len(habits)}

  def habits_pause(name: str) -> dict:
    """Pause a habit (stop it from firing until resumed)."""
    habits = load_habits(cfg.project_root)
    for h in habits:
      if h.name == name.strip().lower():
        h.enabled = False
        save_habits(cfg.project_root, habits)
        return {"ok": True, "name": h.name, "enabled": False}
    return {"ok": False, "error": f"habit not found: {name}"}

  def habits_resume(name: str) -> dict:
    """Resume a paused habit."""
    habits = load_habits(cfg.project_root)
    for h in habits:
      if h.name == name.strip().lower():
        h.enabled = True
        save_habits(cfg.project_root, habits)
        return {"ok": True, "name": h.name, "enabled": True}
    return {"ok": False, "error": f"habit not found: {name}"}

  habits_list.__name__ = "habits_list"
  habits_add.__name__ = "habits_add"
  habits_remove.__name__ = "habits_remove"
  habits_pause.__name__ = "habits_pause"
  habits_resume.__name__ = "habits_resume"

  return [habits_list, habits_add, habits_remove, habits_pause, habits_resume]
