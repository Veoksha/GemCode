"""
Single user turn (Claude Code: inner path ≈ `query()` invocation per message).

CLI and tests call `run_turn` with a Runner already bound to app + session service.
"""

from __future__ import annotations

import sys
from threading import Lock

from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.genai import types


_HITL_PROMPT_LOCK = Lock()


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

  collected: list = []

  run_config = (
    RunConfig(max_llm_calls=max_llm_calls) if max_llm_calls is not None else None
  )

  # Apply token-budget reset only once per user turn, even if we must resume
  # across multiple ADK tool-confirmation handoffs.
  state_delta = None
  if cfg is not None and cfg.token_budget:
    from gemcode.config import token_budget_invocation_reset

    state_delta = token_budget_invocation_reset()

  # The first message is plain user text.
  current_message = types.Content(role="user", parts=[types.Part(text=prompt)])

  async def _await_runner_events(*, next_message: types.Content, do_reset: bool):
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

  # Runner handoff loop: if tools request confirmations, we pause here to ask
  # HITL, then send back function responses so ADK can re-execute the tools.
  do_reset = True
  while True:
    events = await _await_runner_events(
      next_message=current_message, do_reset=do_reset
    )
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

  return collected
