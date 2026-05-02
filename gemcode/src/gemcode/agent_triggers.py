"""
Self-Triggering Agents — Agents that subscribe to bus events and auto-activate.

This module enables autonomous agent behavior:
- Agents can register triggers (bus topics they care about)
- When matching events arrive, the agent auto-activates
- Configurable via `.gemcode/triggers.json`
- Integrates with the AgentMesh for execution

Example trigger config (.gemcode/triggers.json):
{
  "triggers": [
    {
      "agent": "verifier",
      "on_topic": "job.report",
      "when": {"status": "finished"},
      "action": "Review the completed job and verify correctness.",
      "cooldown_s": 60,
      "enabled": true
    },
    {
      "agent": "kaira",
      "on_topic": "org.report",
      "when": {"status": "failed"},
      "action": "Diagnose the failure and attempt a fix.",
      "cooldown_s": 30,
      "enabled": true
    }
  ]
}
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig
from gemcode.event_bus import BusMessage, get_bus


@dataclass
class AgentTrigger:
  """A rule that auto-activates an agent when a bus event matches."""
  agent: str  # org member name
  on_topic: str  # bus topic to watch
  when: dict[str, Any] = field(default_factory=dict)  # payload conditions (all must match)
  action: str = ""  # prompt to send to the agent
  cooldown_s: float = 60.0  # minimum seconds between activations
  enabled: bool = True
  last_fired_s: float = 0.0  # internal: last activation timestamp


def triggers_path(project_root: Path) -> Path:
  return project_root / ".gemcode" / "triggers.json"


def load_triggers(project_root: Path) -> list[AgentTrigger]:
  """Load trigger configs from .gemcode/triggers.json."""
  p = triggers_path(project_root)
  if not p.is_file():
    return _default_triggers()
  try:
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
      return _default_triggers()
    raw = data.get("triggers", [])
    if not isinstance(raw, list):
      return _default_triggers()
    out: list[AgentTrigger] = []
    for item in raw:
      if not isinstance(item, dict):
        continue
      out.append(AgentTrigger(
        agent=str(item.get("agent") or "").strip(),
        on_topic=str(item.get("on_topic") or "").strip(),
        when=item.get("when") if isinstance(item.get("when"), dict) else {},
        action=str(item.get("action") or "").strip(),
        cooldown_s=float(item.get("cooldown_s") or 60),
        enabled=bool(item.get("enabled", True)),
      ))
    return [t for t in out if t.agent and t.on_topic and t.action]
  except Exception:
    return _default_triggers()


def save_triggers(project_root: Path, triggers: list[AgentTrigger]) -> None:
  """Save trigger configs."""
  p = triggers_path(project_root)
  p.parent.mkdir(parents=True, exist_ok=True)
  data = {
    "triggers": [
      {
        "agent": t.agent,
        "on_topic": t.on_topic,
        "when": t.when,
        "action": t.action,
        "cooldown_s": t.cooldown_s,
        "enabled": t.enabled,
      }
      for t in triggers
    ]
  }
  p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _default_triggers() -> list[AgentTrigger]:
  """Sensible defaults: verifier checks finished work, kaira retries failures."""
  return [
    AgentTrigger(
      agent="verifier",
      on_topic="job.report",
      when={"status": "finished"},
      action=(
        "A background job just completed. Review its output for correctness. "
        "If there are issues, report them. If it looks good, confirm PASS. "
        "Use the /review pipeline if code changes are involved."
      ),
      cooldown_s=120,
      enabled=True,
    ),
    AgentTrigger(
      agent="kaira",
      on_topic="job.report",
      when={"status": "failed"},
      action=(
        "A background job failed. Diagnose the root cause from the error. "
        "If it's a simple fix (typo, missing import, wrong path), fix it. "
        "If it's complex, report the diagnosis and recommended next steps."
      ),
      cooldown_s=30,
      enabled=True,
    ),
    AgentTrigger(
      agent="verifier",
      on_topic="checkpoint.created",
      when={},
      action=(
        "A checkpoint was just created (files were modified). "
        "Run a quick verification: check syntax, imports, and basic correctness "
        "of the changed files. Report PASS or issues found."
      ),
      cooldown_s=180,
      enabled=False,  # Opt-in: can be noisy for frequent edits
    ),
  ]


def _payload_matches(payload: dict[str, Any], conditions: dict[str, Any]) -> bool:
  """Check if all conditions match the payload (shallow key-value match)."""
  for key, expected in conditions.items():
    actual = payload.get(key)
    if isinstance(expected, str):
      if str(actual or "").strip().lower() != expected.strip().lower():
        return False
    elif actual != expected:
      return False
  return True


class TriggerEngine:
  """
  Watches the event bus and auto-activates agents based on trigger rules.

  Lifecycle:
  - Created once per session (attached to the mesh)
  - Subscribes to all bus topics via wildcard
  - On each message, checks all triggers
  - If a trigger matches and cooldown has passed, enqueues work on the mesh
  """

  def __init__(self, cfg: GemCodeConfig) -> None:
    self.cfg = cfg
    self._triggers = load_triggers(cfg.project_root)
    self._bus = get_bus()
    self._active = False

    # Subscribe to all bus messages
    self._sub = self._bus.subscribe(topic="*", callback=self._on_message)

  def start(self) -> None:
    """Enable trigger processing."""
    self._active = self._is_enabled()

  def stop(self) -> None:
    """Disable trigger processing."""
    self._active = False
    self._sub.unsubscribe()

  def reload(self) -> None:
    """Reload triggers from disk."""
    self._triggers = load_triggers(self.cfg.project_root)

  def _is_enabled(self) -> bool:
    return os.environ.get("GEMCODE_AGENT_TRIGGERS", "1").strip().lower() in (
      "1", "true", "yes", "on",
    )

  async def _on_message(self, msg: BusMessage) -> None:
    """Check all triggers against an incoming bus message."""
    if not self._active:
      return

    now = time.time()

    for trigger in self._triggers:
      if not trigger.enabled:
        continue
      if trigger.on_topic != msg.topic:
        continue
      if trigger.when and not _payload_matches(msg.payload, trigger.when):
        continue
      # Cooldown check
      if (now - trigger.last_fired_s) < trigger.cooldown_s:
        continue

      # Fire the trigger
      trigger.last_fired_s = now
      await self._fire_trigger(trigger, msg)

  async def _fire_trigger(self, trigger: AgentTrigger, msg: BusMessage) -> None:
    """Enqueue work on the mesh for the triggered agent."""
    try:
      from gemcode.agent_mesh import get_mesh

      mesh = get_mesh(self.cfg)
      if mesh is None:
        return

      # Build context from the triggering message
      context = (
        f"Triggered by bus event:\n"
        f"  topic: {msg.topic}\n"
        f"  from: {msg.from_addr}\n"
        f"  payload: {json.dumps(msg.payload, default=str)[:2000]}\n"
      )

      mesh.enqueue(
        prompt=trigger.action,
        priority=3,  # Triggered work is medium-high priority
        member_name=trigger.agent,
        meta={
          "trigger": {
            "topic": msg.topic,
            "agent": trigger.agent,
            "action": trigger.action,
          }
        },
      )
    except Exception:
      pass

  @property
  def triggers(self) -> list[AgentTrigger]:
    return list(self._triggers)


# ── Tools for managing triggers ──────────────────────────────────────────────

def make_trigger_tools(cfg: GemCodeConfig) -> list:
  """Build tools for managing agent triggers."""

  def triggers_list() -> dict:
    """List all configured agent triggers."""
    triggers = load_triggers(cfg.project_root)
    return {
      "ok": True,
      "triggers": [
        {
          "agent": t.agent,
          "on_topic": t.on_topic,
          "when": t.when,
          "action": t.action[:200],
          "cooldown_s": t.cooldown_s,
          "enabled": t.enabled,
        }
        for t in triggers
      ],
    }

  def triggers_add(
    agent: str,
    on_topic: str,
    action: str,
    when: dict | None = None,
    cooldown_s: float = 60,
  ) -> dict:
    """
    Add a new self-trigger rule for an agent.

    When a bus event matching `on_topic` (and optional `when` conditions) arrives,
    the specified agent will automatically activate with the given `action` prompt.

    Args:
      agent: Org member name to activate.
      on_topic: Bus topic to watch (e.g., "job.report", "org.report").
      action: Prompt to send to the agent when triggered.
      when: Optional payload conditions (e.g., {"status": "failed"}).
      cooldown_s: Minimum seconds between activations (default 60).
    """
    triggers = load_triggers(cfg.project_root)
    triggers.append(AgentTrigger(
      agent=agent.strip(),
      on_topic=on_topic.strip(),
      when=when or {},
      action=action.strip(),
      cooldown_s=float(cooldown_s),
      enabled=True,
    ))
    save_triggers(cfg.project_root, triggers)
    return {"ok": True, "total_triggers": len(triggers)}

  def triggers_remove(agent: str, on_topic: str = "") -> dict:
    """Remove trigger(s) for an agent (optionally filtered by topic)."""
    triggers = load_triggers(cfg.project_root)
    before = len(triggers)
    if on_topic:
      triggers = [t for t in triggers if not (t.agent == agent and t.on_topic == on_topic)]
    else:
      triggers = [t for t in triggers if t.agent != agent]
    save_triggers(cfg.project_root, triggers)
    return {"ok": True, "removed": before - len(triggers), "remaining": len(triggers)}

  triggers_list.__name__ = "triggers_list"
  triggers_add.__name__ = "triggers_add"
  triggers_remove.__name__ = "triggers_remove"

  return [triggers_list, triggers_add, triggers_remove]
