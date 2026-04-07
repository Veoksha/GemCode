"""
Immutable gate snapshot at query entry (cf. `query/config.ts`).

Some codebases use compile-time feature flags; we use env + this struct.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _truthy(name: str, default: bool = False) -> bool:
  v = os.environ.get(name)
  if v is None:
    return default
  return v.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class QueryGates:
  """Runtime gates (env), snapshotted once per invocation."""

  emit_tool_use_summaries: bool
  fast_mode_enabled: bool
  streaming_tool_execution: bool


def build_query_gates() -> QueryGates:
  """Snapshot env-driven gates (no network)."""
  return QueryGates(
      emit_tool_use_summaries=_truthy("GEMCODE_EMIT_TOOL_USE_SUMMARIES"),
      fast_mode_enabled=not _truthy("GEMCODE_DISABLE_FAST_MODE"),
      streaming_tool_execution=_truthy("GEMCODE_STREAMING_TOOL_EXEC", default=True),
  )
