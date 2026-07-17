"""Habit chains — scheduled tasks that fire when another habit completes."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from gemcode.agent_habits import Habit, load_habits, save_habits
from gemcode.config import GemCodeConfig
from gemcode.event_bus import BusMessage, get_bus


def _enabled() -> bool:
  return os.environ.get("GEMCODE_HABIT_CHAINS", "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
  )


def _status_matches(trigger_on: str, actual: str) -> bool:
  want = (trigger_on or "finished").strip().lower()
  got = (actual or "").strip().lower()
  if want in ("", "any", "*"):
    return True
  return got == want


def render_chain_prompt(
  prompt: str,
  *,
  source_habit: str,
  source_status: str,
  source_report: str = "",
  source_error: str = "",
) -> str:
  """Substitute template vars from the upstream habit run."""
  body = (source_report or source_error or "").strip()
  out = (prompt or "").replace("{{source_habit}}", source_habit)
  out = out.replace("{{source_status}}", source_status)
  out = out.replace("{{source_report}}", body)
  out = out.replace("{{report}}", body)
  if body and "{{" not in prompt and body not in prompt:
    out = (
      f"{out.rstrip()}\n\n"
      f"---\n"
      f"Output from upstream task `{source_habit}` ({source_status}):\n"
      f"{body}"
    )
  return out


def habits_watching(source_name: str, habits: list[Habit] | None = None) -> list[Habit]:
  """Return enabled trigger-habits that listen for ``source_name`` completions."""
  src = (source_name or "").strip().lower()
  if not src:
    return []
  if habits is None:
    return []
  out: list[Habit] = []
  for h in habits:
    if not h.enabled:
      continue
    after = (h.trigger_after or "").strip().lower()
    if after and after == src:
      out.append(h)
  return out


def would_create_habit_cycle(
  habits: list[Habit],
  *,
  name: str,
  trigger_after: str,
) -> bool:
  """True if adding ``name`` → ``trigger_after`` closes a directed cycle."""
  nm = (name or "").strip().lower()
  after = (trigger_after or "").strip().lower()
  if not nm or not after:
    return False

  edges: dict[str, str] = {}
  for h in habits:
    if h.name == nm:
      continue
    ta = (h.trigger_after or "").strip().lower()
    if ta:
      edges[h.name] = ta
  edges[nm] = after

  visited: set[str] = set()
  stack: set[str] = set()

  def dfs(node: str) -> bool:
    if node in stack:
      return True
    if node in visited:
      return False
    visited.add(node)
    stack.add(node)
    nxt = edges.get(node)
    if nxt and dfs(nxt):
      return True
    stack.remove(node)
    return False

  return dfs(nm)


class HabitChainEngine:
  """
  When a habit-backed mesh job finishes, enqueue downstream trigger-habits.

  Watches ``job.report`` on the in-process bus (same as agent triggers).
  """

  def __init__(self, cfg: GemCodeConfig) -> None:
    self.cfg = cfg
    self._bus = get_bus()
    self._active = False
    self._last_fired: dict[str, float] = {}
    self._sub = self._bus.subscribe(topic="job.report", callback=self._on_job_report)

  def start(self) -> None:
    self._active = _enabled()

  def stop(self) -> None:
    self._active = False
    self._sub.unsubscribe()

  def reload(self) -> None:
    self._last_fired.clear()

  async def _on_job_report(self, msg: BusMessage) -> None:
    if not self._active:
      return
    payload = msg.payload if isinstance(msg.payload, dict) else {}
    hab = payload.get("habit")
    if not isinstance(hab, dict):
      return
    source_name = str(hab.get("name") or "").strip().lower()
    if not source_name:
      return

    status = str(payload.get("status") or "").strip().lower()
    habits = load_habits(self.cfg.project_root)
    watchers = habits_watching(source_name, habits)
    if not watchers:
      return

    now = time.time()
    changed = False
    fired: list[str] = []

    for target in watchers:
      if not _status_matches(target.trigger_on, status):
        continue
      cd = max(0.0, float(target.trigger_cooldown_s or 0))
      last = self._last_fired.get(target.name, 0.0)
      if cd and (now - last) < cd:
        continue
      if target.max_runs is not None and target.run_count >= target.max_runs:
        continue

      prompt = render_chain_prompt(
        target.prompt,
        source_habit=source_name,
        source_status=status,
        source_report=str(payload.get("report") or ""),
        source_error=str(payload.get("error") or ""),
      )

      try:
        from gemcode.agent_mesh import get_mesh

        mesh = get_mesh(self.cfg)
        if mesh is None:
          continue

        mesh.enqueue(
          prompt=prompt,
          priority=max(target.priority, 1),
          member_name=target.agent,
          meta={
            "habit": {"name": target.name, "agent": target.agent},
            "chain": {
              "after": source_name,
              "on_status": status,
              "source_job_id": str(payload.get("job_id") or ""),
            },
          },
        )
        target.last_run_ms = int(now * 1000)
        target.run_count += 1
        self._last_fired[target.name] = now
        changed = True
        fired.append(target.name)
      except Exception:
        pass

    if changed:
      save_habits(self.cfg.project_root, habits)
      try:
        await self._bus.publish(
          BusMessage(
            topic="habit.chain",
            from_addr="habit-chain",
            payload={
              "source": source_name,
              "status": status,
              "triggered": fired,
            },
          )
        )
      except Exception:
        pass
