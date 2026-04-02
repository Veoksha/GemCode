"""
Transition types for the model↔tool loop (cf. claude-code `query/transitions.ts`).

Terminal: why a turn or invocation ended.
Continue: why another model iteration was scheduled (conceptual; ADK handles scheduling).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Terminal:
  """The loop exited."""

  reason: Literal[
      "completed",
      "blocking_limit",
      "model_error",
      "aborted",
      "prompt_too_long",
      "stop_hook_prevented",
      "max_llm_calls",
      "session_token_limit",
      "tool_circuit_breaker",
  ] | str
  error: object | None = None


@dataclass(frozen=True)
class Continue:
  """Another iteration would run (documentation / logging; ADK runs internally)."""

  reason: Literal[
      "tool_use",
      "reactive_compact_retry",
      "token_budget_continuation",
      "queued_command",
  ] | str
