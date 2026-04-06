"""
Single user turn (Claude Code: inner path ≈ `query()` invocation per message).

CLI and tests call `run_turn` with a Runner already bound to app + session service.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any
from threading import Lock

from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.genai import types


# Delays (seconds) between successive transient-error retries: 2s, 5s, 12s.
# Three retries = up to ~19 seconds of total wait before giving up.
_TRANSIENT_RETRY_DELAYS = [2.0, 5.0, 12.0]


_HITL_PROMPT_LOCK = Lock()


def _events_to_text(events: list[Any]) -> str:
  """Best-effort extraction of assistant text from ADK events."""
  parts: list[str] = []
  for event in events:
    try:
      content = getattr(event, "content", None)
      if not content or not getattr(content, "parts", None):
        continue
      # Omit only user-authored events; model events may have author=None.
      if getattr(event, "author", None) == "user":
        continue
      for part in getattr(content, "parts", []) or []:
        t = getattr(part, "text", None)
        if isinstance(t, str) and t:
          parts.append(t)
    except Exception:
      continue
  return "".join(parts)


def _is_retryable_context_model_error(text: str) -> bool:
  t = (text or "").lower()
  # Key off GemCode's user hint added in `model_errors.py`.
  if "request may be too large" in t:
    return True
  if "gemcode_max_context_chars" in t or "gemcode_tool_result_max_chars" in t:
    return True
  # Fallback heuristics (avoid generic "something broke").
  if "context" in t and ("too large" in t or "token" in t or "length" in t):
    return True
  return False


async def run_turn(
    runner: Runner,
    *,
    user_id: str,
    session_id: str,
    prompt: str,
    max_llm_calls: int | None = None,
    cfg: "GemCodeConfig | None" = None,
) -> list:
  """Execute one user message; collect all Events (caller aggregates text)."""
  run_config = (
    RunConfig(max_llm_calls=max_llm_calls) if max_llm_calls is not None else None
  )

  REQUEST_CONFIRMATION_FC = "adk_request_confirmation"

  def _get_confirmation_requests(events: list) -> list[types.FunctionCall]:
    out: list[types.FunctionCall] = []
    for ev in events:
      try:
        for fc in ev.get_function_calls() or []:
          if getattr(fc, "name", None) == REQUEST_CONFIRMATION_FC:
            out.append(fc)
      except Exception:
        continue
    return out

  def _extract_hint_and_tool(fc: types.FunctionCall) -> tuple[str, str]:
    # generate_request_confirmation_event() builds:
    # - args.originalFunctionCall.{name,args,...}
    # - args.toolConfirmation.{hint, ...}
    tool_name = "unknown_tool"
    hint = ""
    try:
      args = getattr(fc, "args", None) or {}
      orig = args.get("originalFunctionCall") or {}
      tool_name = orig.get("name") or tool_name
      tc = args.get("toolConfirmation") or {}
      hint = tc.get("hint") or ""
    except Exception:
      pass
    return tool_name, hint

  def _prompt_yes_no(prompt_text: str) -> bool:
    with _HITL_PROMPT_LOCK:
      while True:
        ans = input(prompt_text).strip().lower()
        if ans in ("y", "yes"):
          return True
        if ans in ("", "n", "no"):
          return False
        print("Please answer 'y' or 'n'.")

  retry_enabled = os.environ.get("GEMCODE_ENABLE_MODEL_ERROR_RETRY", "1").lower() in (
    "1",
    "true",
    "yes",
    "on",
  )
  retry_max_attempts = int(
    os.environ.get("GEMCODE_MODEL_ERROR_RETRY_MAX_ATTEMPTS", "2")
  )
  retry_shrink_factor = float(
    os.environ.get("GEMCODE_MODEL_ERROR_RETRY_SHRINK_FACTOR", "0.6")
  )
  if retry_max_attempts < 2:
    retry_enabled = False

  orig_ctx_chars: int | None = None
  orig_tool_chars: int | None = None

  try:
    for attempt in range(retry_max_attempts):
      collected: list = []

      # Apply token-budget reset only once per user turn, even if we must
      # resume across multiple ADK tool-confirmation handoffs.
      state_delta = None
      if cfg is not None and cfg.token_budget:
        from gemcode.config import token_budget_invocation_reset

        state_delta = token_budget_invocation_reset()

      # The first message is plain user text.
      current_message = types.Content(
        role="user", parts=[types.Part(text=prompt)]
      )

      async def _await_runner_events(
        *, next_message: types.Content, do_reset: bool
      ):
        kwargs = dict(
          user_id=user_id,
          session_id=session_id,
          new_message=next_message,
        )
        if run_config is not None:
          kwargs["run_config"] = run_config
        if do_reset and state_delta is not None:
          kwargs["state_delta"] = state_delta
        events: list = []
        async for event in runner.run_async(**kwargs):
          events.append(event)
        return events

      # Runner handoff loop: if tools request confirmations, we pause here to
      # ask HITL, then send back function responses so ADK can re-execute the
      # tools.
      #
      # Transient API errors (HTTP 503, 429) are retried here with exponential
      # backoff. on_model_error returns None for these, so the exception
      # propagates from runner.run_async and we catch it below.
      do_reset = True
      transient_attempts = 0
      while True:
        try:
          events = await _await_runner_events(
            next_message=current_message, do_reset=do_reset
          )
        except Exception as _exc:
          from gemcode.model_errors import is_transient_error
          if is_transient_error(_exc) and transient_attempts < len(_TRANSIENT_RETRY_DELAYS):
            delay = _TRANSIENT_RETRY_DELAYS[transient_attempts]
            transient_attempts += 1
            _tui_active = os.environ.get("GEMCODE_TUI_ACTIVE", "0").lower() in ("1", "true", "yes", "on")
            _msg = (
              f"\n[gemcode] Transient API error ({type(_exc).__name__}). "
              f"Retrying in {delay:.0f}s (attempt {transient_attempts}/{len(_TRANSIENT_RETRY_DELAYS)})...\n"
            )
            print(_msg, file=sys.stderr)
            # Surface retry notice in TUI if available.
            if _tui_active:
              try:
                from gemcode.tui import scrollback as _sb
                _sb._transient_retry_notice = _msg  # type: ignore[attr-defined]
              except Exception:
                pass
            await asyncio.sleep(delay)
            # Retry the same message from scratch (session history is intact in SQLite).
            continue
          # Non-transient or out of retries: re-raise so the TUI surfaces it.
          raise

        # Reset transient counter after a successful model call.
        transient_attempts = 0
        collected.extend(events)

        confirmation_fcs = _get_confirmation_requests(events)
        if not confirmation_fcs:
          break

        # If interactive ask is disabled, auto-reject to avoid hanging on stdin.
        interactive_enabled = bool(
          getattr(cfg, "interactive_permission_ask", False)
          and hasattr(sys.stdin, "isatty")
          and sys.stdin.isatty()
        )

        parts: list[types.Part] = []
        for fc in confirmation_fcs:
          tool_name, hint = _extract_hint_and_tool(fc)
          if interactive_enabled:
            suffix = f"\n  Hint: {hint}" if hint else ""
            ok = _prompt_yes_no(
              f"\n[gemcode HITL] Approve tool call '{tool_name}'? [y/N]{suffix}\n> "
            )
          else:
            ok = False
            print(
              f"[gemcode HITL] Tool confirmation requested for '{tool_name}', but interactive-ask is disabled; auto-rejecting.",
              file=sys.stderr,
            )

          parts.append(
            types.Part(
              function_response=types.FunctionResponse(
                name=REQUEST_CONFIRMATION_FC,
                id=getattr(fc, "id", None),
                response={"confirmed": ok},
              )
            )
          )

        current_message = types.Content(role="user", parts=parts)
        # Subsequent resumes must not re-reset token budgets.
        do_reset = False

      # Retry decision: if we detect context/length failures, tighten budgets
      # and re-run once.
      if (
        attempt == 0
        and retry_enabled
        and cfg is not None
        and hasattr(cfg, "max_context_chars")
        and hasattr(cfg, "tool_result_max_chars")
        and attempt + 1 < retry_max_attempts
      ):
        assistant_text = _events_to_text(collected)
        if _is_retryable_context_model_error(assistant_text):
          orig_ctx_chars = cfg.max_context_chars
          orig_tool_chars = cfg.tool_result_max_chars
          cfg.max_context_chars = max(
            50_000, int(orig_ctx_chars * retry_shrink_factor)
          )
          cfg.tool_result_max_chars = max(
            1_000, int(orig_tool_chars * retry_shrink_factor)
          )
          continue

      return collected
  finally:
    if cfg is not None and orig_ctx_chars is not None:
      cfg.max_context_chars = orig_ctx_chars
    if cfg is not None and orig_tool_chars is not None:
      cfg.tool_result_max_chars = orig_tool_chars
