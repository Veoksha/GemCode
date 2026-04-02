"""
Tool catalog and concurrency class (Claude Code–style partitioning, clean-room).

In Claude Code, `tools.ts` registers tools and `toolOrchestration.ts` runs
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
  }
)

# Disk mutations (require --yes / not strict)
MUTATING_TOOLS: frozenset[str] = frozenset(
  {
    "write_file",
    "search_replace",
    "delete_file",
  }
)

# Subprocess (allowlist enforced inside tool)
SHELL_TOOLS: frozenset[str] = frozenset({"run_command"})

# Session planning only (no disk / shell; no extra permission)
PLANNING_TOOLS: frozenset[str] = frozenset({"todo_write"})

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
