"""
Agent Intelligence Layer — Makes GemCode autonomously smart.

In SUPER MODE: Everything runs autonomously. All capabilities enabled.
Agents self-organize, habits auto-create, verification auto-triggers.
No user confirmation needed.

In NORMAL MODE: GemCode proposes smart behaviors and asks "yes/no".
The user stays in control but doesn't have to configure anything manually.

This module handles:
- Auto-enabling capabilities based on project history
- Auto-creating default agents if none exist
- Auto-suggesting habits based on project type
- Auto-triggering verification after risky changes
- Learning from every turn to get smarter over time
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig


def enhance_turn(cfg: GemCodeConfig, prompt: str) -> str:
  """
  Pre-turn intelligence. Runs before the model sees the prompt.

  In super mode: silently enables everything.
  In normal mode: proposes and asks.
  """
  if not _enabled():
    return prompt

  is_super = bool(getattr(cfg, "super_mode", False) or getattr(cfg, "yes_to_all", False))

  # 1. Auto-enable capabilities based on project history
  try:
    _auto_enable_capabilities(cfg, is_super)
  except Exception:
    pass

  # 2. Ensure default org members exist (first run bootstrap)
  try:
    _ensure_default_org(cfg, is_super)
  except Exception:
    pass

  # 3. Auto-enable memory if project has enough history
  try:
    _auto_enable_memory(cfg, is_super)
  except Exception:
    pass

  # 4. Delegation intelligence: suggest routing based on history
  delegation_hint = ""
  try:
    from gemcode.delegation_learning import build_delegation_context
    delegation_hint = build_delegation_context(cfg.project_root, prompt)
  except Exception:
    pass

  if delegation_hint:
    return f"[Delegation intelligence: {delegation_hint}]\n\n" + prompt

  return prompt


def post_turn_learn(cfg: GemCodeConfig, events: list) -> None:
  """
  Post-turn intelligence. Runs after the model completes.

  In super mode: auto-verifies, auto-learns, no questions asked.
  In normal mode: same behavior (learning is always silent).
  """
  if not _enabled():
    return

  is_super = bool(getattr(cfg, "super_mode", False) or getattr(cfg, "yes_to_all", False))

  try:
    _learn_from_events(cfg, events)
  except Exception:
    pass

  try:
    _maybe_trigger_verification(cfg, events, is_super)
  except Exception:
    pass

  # In super mode, auto-suggest habits based on what just happened
  if is_super:
    try:
      _auto_suggest_habits(cfg, events)
    except Exception:
      pass


def first_session_bootstrap(cfg: GemCodeConfig) -> None:
  """
  Called once at the start of a GemCode session.

  In super mode: silently enables all autonomous features.
  In normal mode: proposes features and asks the user.
  """
  is_super = bool(getattr(cfg, "super_mode", False) or getattr(cfg, "yes_to_all", False))

  if is_super:
    # Unlock everything silently
    _unlock_all_powers(cfg)
    return

  # Normal mode: check if this is a fresh project and offer setup
  profile = _load_project_profile(cfg.project_root)
  if profile.get("bootstrapped"):
    return

  # First time — offer autonomous setup
  if _is_interactive():
    _offer_autonomous_setup(cfg)

  profile["bootstrapped"] = True
  _save_project_profile(cfg.project_root, profile)


def _unlock_all_powers(cfg: GemCodeConfig) -> None:
  """Super mode: enable everything without asking."""
  # Enable memory
  if not cfg.enable_memory:
    cfg.enable_memory = True

  # Enable web search
  if not cfg.enable_web_search:
    cfg.enable_web_search = True

  # Enable background learner
  if not getattr(cfg, "enable_background_learner", False):
    try:
      object.__setattr__(cfg, "enable_background_learner", True)
    except Exception:
      cfg.enable_background_learner = True

  # Ensure default org exists
  try:
    from gemcode.org import load_org, resolve_fleet_root
    fleet_root = resolve_fleet_root(cfg.project_root)
    load_org(fleet_root)  # Creates defaults if missing
  except Exception:
    pass

  # Enable triggers
  os.environ.setdefault("GEMCODE_AGENT_TRIGGERS", "1")

  # Enable habits
  os.environ.setdefault("GEMCODE_AGENT_HABITS", "1")

  # Enable auto-verify
  os.environ.setdefault("GEMCODE_AUTO_VERIFY", "1")


def _offer_autonomous_setup(cfg: GemCodeConfig) -> None:
  """Ask the user if they want autonomous features enabled."""
  try:
    print(
      "\n[gemcode] First run detected. GemCode can run autonomously with:\n"
      "  • Memory (remembers across sessions)\n"
      "  • Agent team (verifier + background worker)\n"
      "  • Auto-verification (checks your changes)\n"
      "  • Habits (scheduled recurring tasks)\n"
      "\n"
      "  Enable autonomous mode? [Y/n] ",
      end="",
      flush=True,
      file=sys.stderr,
    )
    ans = ""
    try:
      if hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
        ans = input().strip().lower()
      else:
        ans = "y"
    except (EOFError, KeyboardInterrupt):
      ans = "n"

    if ans in ("", "y", "yes"):
      cfg.enable_memory = True
      cfg.enable_web_search = True
      os.environ["GEMCODE_AGENT_TRIGGERS"] = "1"
      os.environ["GEMCODE_AGENT_HABITS"] = "1"
      os.environ["GEMCODE_AUTO_VERIFY"] = "1"
      print("[gemcode] Autonomous features enabled. Use /super for full autonomy.\n", file=sys.stderr)
    else:
      print("[gemcode] Skipped. Enable later with /super or individual toggles.\n", file=sys.stderr)
  except Exception:
    pass


# ── Internal helpers ──────────────────────────────────────────────────────────

def _enabled() -> bool:
  return os.environ.get("GEMCODE_AGENT_INTELLIGENCE", "1").strip().lower() in (
    "1", "true", "yes", "on",
  )


def _is_interactive() -> bool:
  try:
    return hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
  except Exception:
    return False


def _auto_enable_capabilities(cfg: GemCodeConfig, is_super: bool) -> None:
  """Auto-enable capabilities based on project history."""
  profile = _load_project_profile(cfg.project_root)
  if not profile:
    return

  if is_super:
    # Super mode: enable everything the project has used before
    if profile.get("web_search_frequency", 0) > 0:
      cfg.enable_web_search = True
    if profile.get("memory_frequency", 0) > 0:
      cfg.enable_memory = True
  else:
    # Normal mode: only auto-enable after consistent usage
    if profile.get("web_search_frequency", 0) > 3 and not cfg.enable_web_search:
      cfg.enable_web_search = True
    if profile.get("memory_frequency", 0) > 5 and not cfg.enable_memory:
      cfg.enable_memory = True


def _auto_enable_memory(cfg: GemCodeConfig, is_super: bool) -> None:
  """Enable memory if the project has enough turns to benefit."""
  if cfg.enable_memory:
    return
  profile = _load_project_profile(cfg.project_root)
  turns = profile.get("total_turns", 0)
  if is_super or turns >= 5:
    cfg.enable_memory = True


def _ensure_default_org(cfg: GemCodeConfig, is_super: bool) -> None:
  """Ensure default org members exist on first use."""
  try:
    from gemcode.org import list_members, resolve_fleet_root
    fleet_root = resolve_fleet_root(cfg.project_root)
    members = list_members(fleet_root)
    if members:
      return  # Already has members

    # No members yet — create defaults
    if is_super:
      # Super mode: just create them
      from gemcode.org import hire_member
      hire_member(fleet_root, name="kaira", title="BackgroundWorker", kind="kaira_worker",
                  description="Runs background jobs (tests/lint/scans) and reports back.")
      hire_member(fleet_root, name="verifier", title="Verifier", kind="subagent",
                  description="Independent review and sanity checks on changes.")
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


def _maybe_trigger_verification(cfg: GemCodeConfig, events: list, is_super: bool) -> None:
  """Auto-trigger verification after risky changes."""
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

  # Threshold: 3+ file writes or 2+ shell commands
  if writes < 3 and shell_runs < 2:
    return

  if not os.environ.get("GEMCODE_AUTO_VERIFY", "1").strip().lower() in ("1", "true", "yes", "on"):
    return

  # In super mode: just do it. In normal mode: also just do it (verification is safe).
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


def _auto_suggest_habits(cfg: GemCodeConfig, events: list) -> None:
  """
  In super mode: auto-create useful habits based on project type.

  Detects project type from tool usage and creates appropriate habits
  if they don't already exist.
  """
  try:
    from gemcode.agent_habits import load_habits, save_habits, Habit

    habits = load_habits(cfg.project_root)
    existing_names = {h.name for h in habits}

    # Detect project type from files
    root = cfg.project_root
    has_pytest = (root / "pytest.ini").exists() or (root / "pyproject.toml").exists()
    has_package_json = (root / "package.json").exists()

    new_habits: list[Habit] = []

    # Python project: auto-create test-watch habit
    if has_pytest and "test-watch" not in existing_names:
      new_habits.append(Habit(
        name="test-watch",
        agent="kaira",
        prompt="Run pytest -q. If tests fail, report which ones and why. If all pass, say PASS.",
        every_seconds=1800,  # 30 minutes
        priority=1,
      ))

    # Node project: auto-create lint-watch habit
    if has_package_json and "lint-watch" not in existing_names:
      new_habits.append(Habit(
        name="lint-watch",
        agent="kaira",
        prompt="Run npm run lint (or eslint) if available. Report any issues found.",
        every_seconds=3600,  # 1 hour
        priority=0,
      ))

    if new_habits:
      habits.extend(new_habits)
      save_habits(cfg.project_root, habits)
  except Exception:
    pass


# ── Profile persistence ──────────────────────────────────────────────────────

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
