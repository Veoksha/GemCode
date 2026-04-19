"""
Tool catalog and concurrency class (interactive CLI–style partitioning, clean-room).

In many setups, `tools.ts` registers tools and `toolOrchestration.ts` runs
concurrency-safe batches in parallel and serializes mutating work. Here we
classify tools so permissions and docs stay aligned; ADK executes calls as the
model emits them, but we still enforce policy in order (before_tool_callback).
"""

from __future__ import annotations

from typing import Literal

# Mirrors "safe read" tools — can be interleaved / parallel-friendly in principle.
READ_ONLY_TOOLS: frozenset[str] = frozenset(
  {
    "read_file",
    "list_directory",
    "glob_files",
    "grep_content",
    "web_fetch",
  }
)

# Disk mutations (require --yes / not strict)
MUTATING_TOOLS: frozenset[str] = frozenset(
  {
    "write_file",
    "search_replace",
    "delete_file",
    "move_file",
  }
)

# Subprocess (allowlist or bash -c; require --yes / not strict)
SHELL_TOOLS: frozenset[str] = frozenset({"run_command", "bash"})

# Session planning only (no disk / shell; no extra permission)
# think — in-context reasoning scratchpad (no-op, no side effects)
# run_subtask — spawns a sub-agent; inherits parent permission settings
PLANNING_TOOLS: frozenset[str] = frozenset(
  {"todo_write", "think", "run_subtask", "spawn_subtasks"}
)

ToolConcurrency = Literal["parallel_safe", "serial_mutating", "shell"]


def concurrency_class(tool_name: str) -> ToolConcurrency:
  if tool_name in READ_ONLY_TOOLS or tool_name in PLANNING_TOOLS:
    return "parallel_safe"
  if tool_name in MUTATING_TOOLS:
    return "serial_mutating"
  if tool_name in SHELL_TOOLS:
    return "shell"
  # MCP and unknown tools: treat as serial / cautious
  return "serial_mutating"


def is_mutating(tool_name: str) -> bool:
  return tool_name in MUTATING_TOOLS
