"""
Heuristic next-step guidance (post-turn style).

We can't perfectly replicate a full UI-level "next suggestion job" without
extra model calls, but we can produce reliable, deterministic guidance from
GemCode's recorded `terminal_reason`.
"""

from __future__ import annotations

import os

from gemcode.config import GemCodeConfig


def _truthy_env(name: str, *, default: bool = False) -> bool:
  v = os.environ.get(name)
  if v is None:
    return default
  return v.lower() in ("1", "true", "yes", "on")


def build_prompt_suggestion(
  cfg: GemCodeConfig, *, terminal_reason: str
) -> str | None:
  if not _truthy_env("GEMCODE_ENABLE_PROMPT_SUGGESTIONS", default=True):
    return None

  r = terminal_reason
  if r in ("completed", ""):
    return None

  if r == "permission_denied":
    return (
      "Some actions were blocked by policy. Re-run with `--yes` (or add the "
      "needed command to `GEMCODE_ALLOW_COMMANDS` when in strict mode), then "
      "try again with the same request."
    )

  if r == "tool_circuit_breaker":
    return (
      "Tool execution is being halted by the circuit breaker. Start a new "
      "session (`--session <new_id>`), then either fix the failing tool "
      "inputs or increase `GEMCODE_MAX_CONSECUTIVE_TOOL_FAILURES`."
    )

  if r == "session_token_limit":
    return (
      "This session exceeded the token ceiling. Start a new session (new "
      "`--session` id) or raise `GEMCODE_MAX_SESSION_TOKENS`, then re-run "
      "the request."
    )

  if r == "token_budget_stop":
    return (
      "Per-turn token budget was exhausted. Re-run the request (or split it "
      "into smaller steps). If you want more room, increase "
      "`GEMCODE_TOKEN_BUDGET` or reduce the prompt/context."
    )

  if r in ("tool_exception", "tool_retryable_error"):
    return (
      "A tool raised an exception. Check `.gemcode/audit.log` for the tool "
      "error details, then re-run with corrected inputs or fewer/shorter "
      "arguments."
    )

  if r == "model_error":
    return (
      "The model API returned an error (details appear above and in "
      "`.gemcode/audit.log`). Retry, shorten the prompt or session history, "
      "confirm `GOOGLE_API_KEY` and `GEMCODE_MODEL`, or set "
      "`GEMCODE_VERBOSE_MODEL_ERRORS=1` for a full traceback."
    )

  # Generic fallback.
  return (
    f"The run ended with terminal reason `{terminal_reason}`. Check "
    f"`.gemcode/audit.log`, then re-run with a narrower request or after "
    f"adjusting limits/policy flags."
  )

