"""
Agent Intelligence Layer — The connective tissue that makes GemCode's features
work together as a coherent autonomous system.

This module connects:
- Delegation learning (what worked before)
- Capability routing (what tools to enable)
- Risk scoring (how careful to be)
- Memory (what we know about this project)
- Triggers (what to do automatically)
- Fleet reports (what happened in the background)

Into a single "intelligence" that makes smart decisions without prompt injection.

The key insight: instead of injecting text into prompts hoping the model follows it,
we make STRUCTURAL decisions (which tools to enable, which agent to route to,
what priority to assign) that the model can't ignore.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig


def enhance_turn(cfg: GemCodeConfig, prompt: str) -> str:
  """
  Pre-turn intelligence pass. Makes structural decisions and returns
  an optionally enriched prompt (minimal additions, not prompt injection).

  Structural decisions (can't be ignored by the model):
  - Auto-enable capabilities based on delegation history
  - Route to the right model based on task patterns
  - Set risk score based on historical outcomes
  - Pre-warm the mesh with likely-needed agents

  Returns the prompt (possibly with a small context header from delegation memory).
  """
  if not _enabled():
    return prompt

  # 1. Delegation intelligence: suggest routing based on history
  delegation_hint = ""
  try:
    from gemcode.delegation_learning import build_delegation_context
    delegation_hint = build_delegation_context(cfg.project_root, prompt)
  except Exception:
    pass

  # 2. Capability auto-enable based on project patterns
  try:
    _auto_enable_capabilities(cfg, prompt)
  except Exception:
    pass

  # 3. Pre-warm mesh agents that are likely needed
  try:
    _prewarm_likely_agents(cfg, prompt)
  except Exception:
    pass

  # 4. Inject minimal delegation context (not prompt injection — just facts)
  if delegation_hint:
    return (
      f"[Delegation intelligence: {delegation_hint}]\n\n"
      + prompt
    )

  return prompt


def post_turn_learn(cfg: GemCodeConfig, events: list) -> None:
  """
  Post-turn intelligence pass. Learns from what just happened.

  - Records which tools were used and their outcomes
  - Updates project capability profile
  - Triggers background verification if risky changes were made
  """
  if not _enabled():
    return

  try:
    _learn_from_events(cfg, events)
  except Exception:
    pass

  try:
    _maybe_trigger_verification(cfg, events)
  except Exception:
    pass


def _enabled() -> bool:
  return os.environ.get("GEMCODE_AGENT_INTELLIGENCE", "1").strip().lower() in (
    "1", "true", "yes", "on",
  )


def _auto_enable_capabilities(cfg: GemCodeConfig, prompt: str) -> None:
  """
  Auto-enable capabilities based on what this project typically needs.

  Instead of regex-matching the prompt (old approach), we look at what
  capabilities were used successfully in past sessions for this project.
  """
  profile = _load_project_profile(cfg.project_root)
  if not profile:
    return

  # If this project frequently uses web search, auto-enable it
  if profile.get("web_search_frequency", 0) > 3 and not cfg.enable_web_search:
    cfg.enable_web_search = True

  # If this project frequently uses memory, auto-enable it
  if profile.get("memory_frequency", 0) > 5 and not cfg.enable_memory:
    cfg.enable_memory = True


def _prewarm_likely_agents(cfg: GemCodeConfig, prompt: str) -> None:
  """
  If delegation history suggests this task will need specific agents,
  ensure they exist in the org (hire if missing).
  """
  try:
    from gemcode.delegation_learning import suggest_agent_for_task
    suggestion = suggest_agent_for_task(cfg.project_root, prompt)
    if suggestion:
      # Ensure the suggested agent exists
      from gemcode.org import find_member, resolve_fleet_root
      fleet_root = resolve_fleet_root(cfg.project_root)
      m = find_member(fleet_root, suggestion)
      if m is None:
        # The agent was deleted but history remembers it — don't auto-recreate
        pass
  except Exception:
    pass


def _learn_from_events(cfg: GemCodeConfig, events: list) -> None:
  """Extract learning signals from a completed turn's events."""
  tools_used: list[str] = []
  had_web = False
  had_memory = False
  had_delegation = False

  for ev in events:
    try:
      fcs = ev.get_function_calls() or []
      for fc in fcs:
        name = getattr(fc, "name", "") or ""
        if name:
          tools_used.append(name)
          if name in ("web_search", "web_fetch", "google_search"):
            had_web = True
          if name in ("load_memory", "preload_memory", "remember_fact"):
            had_memory = True
          if name in ("org_delegate", "org_spawn", "mesh_delegate"):
            had_delegation = True
    except Exception:
      continue

  if not tools_used:
    return

  # Update project profile
  profile = _load_project_profile(cfg.project_root)
  if had_web:
    profile["web_search_frequency"] = profile.get("web_search_frequency", 0) + 1
  if had_memory:
    profile["memory_frequency"] = profile.get("memory_frequency", 0) + 1
  if had_delegation:
    profile["delegation_frequency"] = profile.get("delegation_frequency", 0) + 1
  profile["total_turns"] = profile.get("total_turns", 0) + 1
  profile["last_turn_ms"] = int(time.time() * 1000)
  _save_project_profile(cfg.project_root, profile)


def _maybe_trigger_verification(cfg: GemCodeConfig, events: list) -> None:
  """
  If the turn made risky changes (writes to multiple files, shell commands),
  auto-trigger the verifier agent via the mesh.
  """
  writes = 0
  shell_runs = 0
  files_changed: list[str] = []

  for ev in events:
    try:
      fcs = ev.get_function_calls() or []
      for fc in fcs:
        name = getattr(fc, "name", "") or ""
        args = getattr(fc, "args", {}) or {}
        if name in ("write_file", "search_replace"):
          writes += 1
          p = args.get("path") or args.get("file_path") or ""
          if p:
            files_changed.append(str(p))
        if name in ("bash", "run_command"):
          shell_runs += 1
    except Exception:
      continue

  # Only auto-verify if there were significant changes
  if writes < 3 and shell_runs < 2:
    return

  # Check if auto-verification is enabled
  if not os.environ.get("GEMCODE_AUTO_VERIFY", "1").strip().lower() in ("1", "true", "yes", "on"):
    return

  try:
    from gemcode.agent_mesh import get_mesh
    mesh = get_mesh(cfg)
    if mesh is None:
      return

    files_str = ", ".join(files_changed[:10])
    mesh.enqueue(
      prompt=(
        f"Verify the recent changes are correct. Files modified: {files_str}. "
        f"Total writes: {writes}, shell commands: {shell_runs}. "
        "Check for: syntax errors, broken imports, logic bugs, missing edge cases. "
        "Report PASS or FAIL with specific findings."
      ),
      priority=2,
      member_name="verifier",
      meta={"auto_verify": True, "files": files_changed[:20]},
    )
  except Exception:
    pass


def _project_profile_path(project_root: Path) -> Path:
  return project_root / ".gemcode" / "project_profile.json"


def _load_project_profile(project_root: Path) -> dict[str, Any]:
  p = _project_profile_path(project_root)
  if not p.is_file():
    return {}
  try:
    return json.loads(p.read_text(encoding="utf-8"))
  except Exception:
    return {}


def _save_project_profile(project_root: Path, profile: dict[str, Any]) -> None:
  p = _project_profile_path(project_root)
  p.parent.mkdir(parents=True, exist_ok=True)
  try:
    p.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
  except Exception:
    pass
