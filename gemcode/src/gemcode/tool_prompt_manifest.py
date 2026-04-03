"""
Tool system prompt manifest.

Claude Code's "tool system" approach is largely prompt-driven: it provides the
model a clear, consistent contract about what tools exist, what categories they
fall into, and what the model is allowed to do with each tool.

GemCode primarily enforces policy via ADK callbacks, but adding a short,
deterministic tool manifest to the model instruction improves behavior and
reduces tool-call mistakes.

This module builds a compact manifest string from GemCodeConfig.
"""

from __future__ import annotations

import os
from typing import Iterable

from gemcode.config import GemCodeConfig
from gemcode.tool_registry import MUTATING_TOOLS, PLANNING_TOOLS, READ_ONLY_TOOLS, SHELL_TOOLS


def _truthy_env(name: str, *, default: bool = False) -> bool:
  v = os.environ.get(name)
  if v is None:
    return default
  return v.lower() in ("1", "true", "yes", "on")


def _cap(s: str, *, max_chars: int) -> str:
  if len(s) <= max_chars:
    return s
  return s[: max_chars - 3] + "..."


def _fmt_list(items: Iterable[str]) -> str:
  xs = [x for x in items if x]
  xs.sort(key=lambda a: a.lower())
  return ", ".join(xs)


def build_tool_manifest(cfg: GemCodeConfig) -> str | None:
  """
  Returns a compact "tool system" section to append to the LLM instruction,
  or None if disabled.
  """
  enabled = _truthy_env("GEMCODE_ENABLE_TOOL_SYSTEM_PROMPT", default=True)
  if not enabled:
    return None

  max_chars = int(os.environ.get("GEMCODE_TOOL_SYSTEM_PROMPT_MAX_CHARS", "3200"))

  permission_mode = getattr(cfg, "permission_mode", "default")
  yes_to_all = bool(getattr(cfg, "yes_to_all", False))
  interactive_ask_on = bool(getattr(cfg, "interactive_permission_ask", False))
  sticky_hitl = bool(getattr(cfg, "interactive_hitl_sticky_session", True))

  # Core custom tools.
  read_only = sorted(READ_ONLY_TOOLS)
  mutating = sorted(MUTATING_TOOLS)
  shell = sorted(SHELL_TOOLS)
  planning = sorted(PLANNING_TOOLS)

  # Deep research built-ins are not always safe to combine; we only describe
  # what's actually enabled by config.
  deep_research_on = bool(getattr(cfg, "enable_deep_research", False))
  embeddings_on = bool(getattr(cfg, "enable_embeddings", False))
  computer_on = bool(getattr(cfg, "enable_computer_use", False))

  maps_grounding_on = bool(getattr(cfg, "enable_maps_grounding", False))
  tool_comb_mode = getattr(cfg, "tool_combination_mode", None) or "deep_research"

  allow_cmds = getattr(cfg, "allow_commands", None)
  allow_cmds_str = ""
  if allow_cmds:
    allow_cmds_str = _fmt_list(list(allow_cmds))

  # Provide Gemini 3 tool-context circulation contract (best-effort).
  # We can't guarantee exact tool combination semantics, but the model can
  # align expectations with GemCode's behavior.
  gemini3_combination_contract = (
    f"tool-context-circulation mode is {tool_comb_mode}. "
    "When enabled, built-in tool results may appear in context for subsequent function tool calls."
  )

  mutating_policy_extra = ""
  if interactive_ask_on:
    mutating_policy_extra = (
      " If user_in_run_hitl_prompt_enabled is true, the session will show an inline approval prompt—"
      "do **not** tell the user to re-run with `--yes`; wait for approval and proceed."
    )
  else:
    mutating_policy_extra = (
      " If neither --yes nor inline HITL is available, ask the user to re-run with `--yes` when mutations are required."
    )

  manifest = f"""## Tool system (GemCode)

Model behavior:
- Issue **multiple tool calls in one assistant step** when calls are independent (e.g. several `read_file`/`glob_files`/`grep_content` in parallel). Use a single tool call when the next action depends on the previous result.
- **Reason end-to-end:** you decide which tools to call and in what order—the user expects autonomous execution within policy, not a questionnaire.

Permission policy:
- permission_mode={permission_mode}
- user_confirmation_provided(--yes)={yes_to_all}
- user_in_run_hitl_prompt_enabled={interactive_ask_on}
- session_sticky_hitl={sticky_hitl}: when true, after the user approves **one** tool in this session, further mutating/shell tools may run without re-prompting (set GEMCODE_HITL_STICKY_SESSION=0 to prompt every time).

You may call tools as follows:
- Session planning: {_fmt_list(planning)}. In-memory task list for this session (no disk writes). Use for non-trivial multi-step work; set merge=true to upsert by id.
- Read-only tools: {_fmt_list(read_only)}.
  Use these proactively to locate files and code before asking the user for paths.
- Mutating tools (WRITE/EDIT/DELETE): {_fmt_list(mutating)}.
  Only call if user_confirmation_provided(--yes) is true OR user_in_run_hitl_prompt_enabled is true.{mutating_policy_extra}
- Shell tool (run_command): {_fmt_list(shell)}.
  Prefer allowlisted commands (GEMCODE_ALLOW_COMMANDS). Allowed={allow_cmds_str or "<none>"}.
  If the user approves a run_command in the session prompt, that specific invocation may run even when the executable is not on the default list.
  Only call if user_confirmation_provided(--yes) is true OR user_in_run_hitl_prompt_enabled is true.
  Notes:
  - Prefer `python -m pip ...` (or `python3 -m pip ...`) so installs stay in the active virtualenv.
  - Do not assume sudo/system package manager access.
  - `run_command` supports `cwd_subdir` (relative path under the project) instead of `cd`/`bash`; use parallel `extra_env_keys` / `extra_env_values` (e.g. ["CI"] and ["1"]) for non-interactive installers; use `background=true` for long-running dev servers.

Optional capability tools:
- Deep research built-ins are {'ON' if deep_research_on else 'OFF'}.
  Active built-ins: google_search, url_context{', google_maps' if deep_research_on and maps_grounding_on else ''}.
  {gemini3_combination_contract}
- Embeddings semantic retrieval is {'ON' if embeddings_on else 'OFF'}:
  semantic_search_files.
- Computer use is {'ON' if computer_on else 'OFF'}:
  browser automation actions via Computer Use toolset.
  Only call if permission_mode != strict AND (user_confirmation_provided(--yes) is true OR user_in_run_hitl_prompt_enabled is true).

If a tool call is rejected by policy, do NOT retry the same mutation without the required user confirmation.
"""

  return _cap(manifest, max_chars=max_chars)

