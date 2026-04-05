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

  max_chars = int(os.environ.get("GEMCODE_TOOL_SYSTEM_PROMPT_MAX_CHARS", "6000"))

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

  memory_on = bool(getattr(cfg, "enable_memory", False))

  manifest = f"""## Tool system (GemCode)

### Execution model
- Issue **multiple independent tool calls in one step** (parallel reads, parallel grep, parallel run_subtask). Use sequential calls only when step B needs step A's result.
- **Reason end-to-end autonomously.** The user expects complete tasks, not a questionnaire. Use `think` before complex actions, `todo_write` to track multi-step work.
- **Never stop after the first tool call succeeds.** Keep going until the full task is done or you hit a genuine blocker.

### Permission policy
| Setting | Value |
|---------|-------|
| permission_mode | {permission_mode} |
| --yes (auto-approve mutations) | {yes_to_all} |
| interactive HITL prompt | {interactive_ask_on} |
| sticky HITL (approve once → whole session) | {sticky_hitl} |

{mutating_policy_extra.strip()}

### Planning & reasoning tools: {_fmt_list(planning)}
- **`todo_write(todos, merge)`** — in-memory task tracker.
  - `todos`: list of objects with `id` (str), `content` (str), `status` (one of: `pending`, `in_progress`, `completed`, `cancelled`).
  - `merge=false` (default): replace the entire list. `merge=true`: upsert by id (update existing, add new, leave others unchanged).
  - Workflow: create with status=pending at task start → update to in_progress when you begin → completed when done.
  - Use for ANY task with 3+ distinct steps.
- **`think(thought)`** — private reasoning scratchpad. Not shown to user. Use before: complex refactors, debugging hypotheses, destructive actions, architecture decisions.
- **`run_subtask(task, context)`** — spawn an isolated sub-agent with a fresh context window.
  - Inherits your permissions and tool set (except run_subtask itself — no recursion).
  - Returns the sub-agent's final text as `result`.
  - Use for: context-heavy exploration (reading 50+ files), parallel investigation of independent subsystems, verification passes after you finish work.
  - Always give the sub-agent enough context to work independently; end the task with "Summarise your findings clearly."

### Read-only tools (no permission needed): {_fmt_list(read_only)}
Use proactively. Never use bash/run_command just to list or read files — these are instant and require zero approval.

### Mutating tools (WRITE/EDIT/DELETE): {_fmt_list(mutating)}
Require: `--yes` OR inline HITL approval.
- `write_file(path, content)` — create or overwrite. Always `read_file` first if the file exists.
- `search_replace(path, old_string, new_string)` — targeted in-place edit. `old_string` must be unique in the file; include 3+ lines of context.
- `delete_file(path)`, `move_file(src, dest)` — destructive; think before calling.

### Shell tools: {_fmt_list(shell)}
Require: `--yes` OR inline HITL approval. Allowed commands: {allow_cmds_str or "(default allowlist)"}.
- `bash(command, timeout_seconds, cwd_subdir, background)` — full shell with pipes, redirects.
  - `background=true` for dev servers, watchers, long builds.
  - Use `2>&1 | tail -N` to cap verbose output.
- `run_command(command, args, cwd_subdir, background, extra_env_keys, extra_env_values)` — single-executable without shell features.
  - Use `extra_env_keys`/`extra_env_values` for non-interactive environment injection.
  - Prefer `python -m pip` over `pip` to stay in the active virtualenv.

### Optional capability tools
| Capability | Status | Tools available |
|-----------|--------|----------------|
| Deep research | {'**ON**' if deep_research_on else 'off'} | {('google_search, url_context' + (', google_maps_grounding' if maps_grounding_on else '')) if deep_research_on else '—'} |
| Embeddings / semantic search | {'**ON**' if embeddings_on else 'off'} | {'semantic_search_files' if embeddings_on else '—'} |
| Persistent memory | {'**ON**' if memory_on else 'off'} | {'preload_memory (auto-injected), memory loaded from .gemcode/memories.jsonl' if memory_on else '— (enable with /memory on)'} |
| Browser / computer use | {'**ON**' if computer_on else 'off'} | {'navigate, click_at, type_text_at, browser_screenshot, browser_find_element, scroll_at, key_combination, ...' if computer_on else '— (enable with /computer on)'} |

{gemini3_combination_contract}

Enable any capability mid-session with: `/research on`, `/embeddings on`, `/memory on`, `/computer on`.

### Policy
If a tool call is rejected, do NOT retry the same mutation without the required user confirmation. Adjust your plan and report clearly.
"""

  return _cap(manifest, max_chars=max_chars)

