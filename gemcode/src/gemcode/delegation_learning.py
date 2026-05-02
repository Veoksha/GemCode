"""
Delegation Learning — Remember successful agent patterns.

When an org delegation succeeds, this module:
1. Records the pattern (task type → agent → outcome)
2. Builds a "delegation memory" that future turns can reference
3. Suggests optimal agent routing based on past successes

Storage: .gemcode/delegation_memory.jsonl
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig
from gemcode.event_bus import BusMessage, get_bus


def _memory_path(project_root: Path) -> Path:
  d = project_root / ".gemcode"
  d.mkdir(parents=True, exist_ok=True)
  return d / "delegation_memory.jsonl"


def enabled() -> bool:
  return os.environ.get("GEMCODE_DELEGATION_LEARNING", "1").strip().lower() in (
    "1", "true", "yes", "on",
  )


def record_delegation(
  project_root: Path,
  *,
  member: str,
  task: str,
  status: str,
  result_summary: str = "",
  duration_ms: int = 0,
) -> None:
  """Record a delegation outcome for future learning."""
  if not enabled():
    return
  try:
    entry = {
      "ts_ms": int(time.time() * 1000),
      "member": member,
      "task_prefix": task[:500],
      "status": status,
      "result_summary": result_summary[:500],
      "duration_ms": duration_ms,
    }
    p = _memory_path(project_root)
    with open(p, "a", encoding="utf-8") as f:
      f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
  except Exception:
    pass


def load_delegation_history(
  project_root: Path,
  *,
  limit: int = 100,
  member: str | None = None,
  status: str | None = None,
) -> list[dict[str, Any]]:
  """Load recent delegation history, optionally filtered."""
  p = _memory_path(project_root)
  if not p.is_file():
    return []
  try:
    lines = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    entries: list[dict[str, Any]] = []
    for line in reversed(lines):
      if not line.strip():
        continue
      try:
        entry = json.loads(line)
      except Exception:
        continue
      if not isinstance(entry, dict):
        continue
      if member and str(entry.get("member") or "") != member:
        continue
      if status and str(entry.get("status") or "") != status:
        continue
      entries.append(entry)
      if len(entries) >= limit:
        break
    return entries
  except Exception:
    return []


def suggest_agent_for_task(project_root: Path, task: str) -> str | None:
  """
  Suggest the best agent for a task based on past delegation successes.

  Returns the member name with the highest success rate for similar tasks,
  or None if no history exists.
  """
  if not enabled():
    return None

  history = load_delegation_history(project_root, limit=200, status="finished")
  if not history:
    return None

  # Simple keyword matching: find entries whose task_prefix overlaps with the new task
  import re
  task_words = set(re.findall(r'\b\w{4,}\b', task.lower()))
  if not task_words:
    return None

  scores: dict[str, float] = {}
  counts: dict[str, int] = {}

  for entry in history:
    member = str(entry.get("member") or "")
    if not member:
      continue
    prefix = str(entry.get("task_prefix") or "").lower()
    prefix_words = set(re.findall(r'\b\w{4,}\b', prefix))
    overlap = len(task_words & prefix_words)
    if overlap == 0:
      continue
    # Score: overlap ratio * recency bonus
    score = overlap / max(len(task_words), 1)
    scores[member] = scores.get(member, 0.0) + score
    counts[member] = counts.get(member, 0) + 1

  if not scores:
    return None

  # Normalize by count (prefer consistent performers)
  best = max(scores.keys(), key=lambda m: scores[m] / max(counts[m], 1))
  return best if scores[best] > 0.3 else None


def build_delegation_context(project_root: Path, task: str) -> str:
  """
  Build a context string about past delegations for the current task.

  Injected into the agent's prompt so it can make informed delegation decisions.
  """
  if not enabled():
    return ""

  suggestion = suggest_agent_for_task(project_root, task)
  history = load_delegation_history(project_root, limit=10, status="finished")

  if not suggestion and not history:
    return ""

  parts: list[str] = []
  if suggestion:
    parts.append(f"Delegation hint: '{suggestion}' has handled similar tasks successfully before.")

  if history:
    recent = history[:5]
    lines = []
    for h in recent:
      lines.append(f"  - {h.get('member')}: {h.get('task_prefix', '')[:80]}... → {h.get('status')}")
    parts.append("Recent delegation history:\n" + "\n".join(lines))

  return "\n".join(parts)


class DelegationLearner:
  """
  Subscribes to org.report events and records outcomes for learning.

  Integrates with the event bus — no daemon required.
  """

  def __init__(self, cfg: GemCodeConfig) -> None:
    self.cfg = cfg
    self._bus = get_bus()
    self._active = enabled()

    if self._active:
      self._bus.subscribe(topic="org.report", callback=self._on_org_report)
      self._bus.subscribe(topic="job.report", callback=self._on_job_report)

  async def _on_org_report(self, msg: BusMessage) -> None:
    """Record org delegation outcomes."""
    if not self._active:
      return
    try:
      payload = msg.payload
      status = str(payload.get("status") or "")
      if status not in ("finished", "failed"):
        return

      member_dict = payload.get("member")
      member_name = ""
      if isinstance(member_dict, dict):
        member_name = str(member_dict.get("name") or member_dict.get("address") or "")
      if not member_name:
        member_name = msg.from_addr

      task = str(payload.get("task") or "")
      result = payload.get("result")
      result_summary = ""
      if isinstance(result, dict):
        result_summary = str(result.get("report") or result.get("result") or "")[:500]
      elif isinstance(result, str):
        result_summary = result[:500]

      record_delegation(
        self.cfg.project_root,
        member=member_name,
        task=task,
        status=status,
        result_summary=result_summary,
      )
    except Exception:
      pass

  async def _on_job_report(self, msg: BusMessage) -> None:
    """Record job outcomes (for non-org delegations)."""
    if not self._active:
      return
    try:
      payload = msg.payload
      status = str(payload.get("status") or "")
      if status not in ("finished", "failed"):
        return

      member = str(payload.get("member") or msg.from_addr or "worker")
      report = str(payload.get("report") or "")[:500]

      record_delegation(
        self.cfg.project_root,
        member=member,
        task=f"(job {payload.get('job_id', '')})",
        status=status,
        result_summary=report,
      )
    except Exception:
      pass
