"""
ADK callbacks: permissions, audit, tool failure circuit breaker, usage logging.

Maps to Claude Code patterns:
- before_tool / after_tool ≈ permission gates + telemetry around tool execution
- after_model ≈ cost / usage hooks (see cost-tracker.ts role)
- Session state for streak counters ≈ autoCompact failure tracking (MVP: tool errors)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from google.adk.tools.base_tool import BaseTool

from gemcode.audit import append_audit
from gemcode.config import GemCodeConfig
from gemcode.context_budget import truncate_tool_result_dict
from gemcode.context_warning import calculate_context_warning_state, worst_alert_level
from gemcode.limits import SESSION_TOTAL_TOKENS_KEY
from gemcode.query.token_budget import BudgetTracker, check_token_budget, create_budget_tracker
from gemcode.hitl_session import HITL_STICKY_SESSION_KEY
from gemcode.model_errors import format_model_error_for_user
from gemcode.tool_registry import MUTATING_TOOLS, SHELL_TOOLS
from gemcode.tools.shell_gate import arm_confirmed_shell_basename

_STATE_FAILURE_KEY = "gemcode:consecutive_tool_failures"
TERMINAL_REASON_KEY = "gemcode:terminal_reason"
_BT_BASE_TOTAL_TOKENS = "gemcode:bt_base_total_tokens"
_BT_TOKEN_BUDGET_STOP = "gemcode:bt_token_budget_stop"
_ERROR_KIND_PERMISSION_DENIED = "permission_denied"
_ERROR_KIND_CIRCUIT_BREAKER = "circuit_breaker"
_ERROR_KIND_TOOL_EXCEPTION = "tool_exception"
_ERROR_KIND_PERMISSION_BLOCK = "permission_block"
_BT_CC = "gemcode:bt_cc"
_BT_LD = "gemcode:bt_ld"
_BT_LG = "gemcode:bt_lg"
_BT_T0 = "gemcode:bt_t0"
_CTX_WARN_LEVEL_NOTIFIED = "gemcode:ctx_warn_level_notified"
_LAST_PROMPT_TOKENS = "gemcode:last_prompt_tokens"
_LAST_CONTEXT_PCT = "gemcode:last_context_percent_left"
_LAST_CONTEXT_LEVEL = "gemcode:last_context_alert_level"

def _truthy_env(name: str, *, default: bool = False) -> bool:
  v = os.environ.get(name)
  if v is None:
    return default
  return v.lower() in ("1", "true", "yes", "on")


def _maybe_tool_summary_enabled() -> bool:
  # Mirrors Claude's "emit tool use summaries" gate conceptually.
  return _truthy_env("GEMCODE_EMIT_TOOL_USE_SUMMARIES", default=False)


def _redact_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
  out = dict(args)
  if "content" in out and isinstance(out["content"], str) and len(out["content"]) > 2000:
    out["content"] = out["content"][:2000] + "[...]"
  return out


def _max_consecutive_failures() -> int:
  return int(os.environ.get("GEMCODE_MAX_CONSECUTIVE_TOOL_FAILURES", "8"))


def _arm_shell_from_args(args: dict[str, Any]) -> None:
  cmd = args.get("command")
  if isinstance(cmd, str) and cmd.strip():
    arm_confirmed_shell_basename(Path(cmd.strip()).name)


def _is_computer_use_tool(tool: BaseTool) -> bool:
  """
  Detect ADK ComputerUseTool instances without enumerating every method name.

  ADK tool objects are named after the BaseComputer method (e.g. `click_at`),
  so we detect by the tool class/module instead of by `tool.name`.
  """
  try:
    cls = tool.__class__
    mod = getattr(cls, "__module__", "") or ""
    name = getattr(cls, "__name__", "") or ""
    return name == "ComputerUseTool" and "computer_use" in mod
  except Exception:
    return False


def make_before_tool_callback(cfg: GemCodeConfig):
  """Permission gate + circuit breaker (open after too many tool errors in a row)."""

  def _hitl_sticky_enabled(tool_context) -> bool:
    try:
      return bool(
          getattr(cfg, "interactive_hitl_sticky_session", False)
          and tool_context is not None
          and tool_context.state.get(HITL_STICKY_SESSION_KEY)
      )
    except Exception:
      return False

  def _hitl_mark_session_approved(tool_context) -> None:
    if not getattr(cfg, "interactive_hitl_sticky_session", False):
      return
    try:
      if tool_context is not None:
        tool_context.state[HITL_STICKY_SESSION_KEY] = True
    except Exception:
      pass

  def _tool_confirmation_state(tool_context) -> bool | None:
    """
    Returns:
      - True  => tool call was confirmed by the user for this invocation context
      - False => user explicitly rejected
      - None  => no confirmation info present
    """
    if tool_context is None:
      return None
    try:
      tc = getattr(tool_context, "tool_confirmation", None)
      if tc is None:
        return None
      confirmed = getattr(tc, "confirmed", None)
      if confirmed is None:
        return None
      return bool(confirmed)
    except Exception:
      return None

  def before_tool(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context,
  ) -> dict[str, Any] | None:
    name = getattr(tool, "name", None) or ""
    is_computer_tool = _is_computer_use_tool(tool)
    record = {"tool": name, "args": _redact_args(name, args)}
    append_audit(cfg.project_root, record)

    streak = 0
    if tool_context is not None:
      try:
        st = tool_context.state
        streak = st.get(_STATE_FAILURE_KEY, 0)
      except Exception:
        pass

    if streak >= _max_consecutive_failures():
      if tool_context is not None:
        try:
          st = tool_context.state
          if not st.get(TERMINAL_REASON_KEY):
            st[TERMINAL_REASON_KEY] = "tool_circuit_breaker"
        except Exception:
          pass
      return {
        "error": (
          f"Stopped after {streak} consecutive tool failures (circuit breaker). "
          "Start a new session or fix the underlying issue."
        ),
        "error_kind": _ERROR_KIND_CIRCUIT_BREAKER,
      }

    if name in MUTATING_TOOLS or is_computer_tool:
      if cfg.permission_mode == "strict":
        if is_computer_tool:
          return {
            "error": "strict mode: computer use disabled",
            "error_kind": _ERROR_KIND_PERMISSION_DENIED,
          }
        return {
          "error": "strict mode: file writes disabled",
          "error_kind": _ERROR_KIND_PERMISSION_DENIED,
        }
      if not cfg.yes_to_all:
        # In-run HITL: request ADK tool confirmation and pause execution until
        # the user approves in the current terminal session.
        if getattr(cfg, "interactive_permission_ask", False):
          # After one approval this ADK session, optional skip (see GEMCODE_HITL_STICKY_SESSION).
          if _hitl_sticky_enabled(tool_context):
            return None
          tc_state = _tool_confirmation_state(tool_context)
          if tc_state is True:
            _hitl_mark_session_approved(tool_context)
            return None
          if tc_state is False:
            return {
                "error": "This tool call was rejected.",
                "error_kind": _ERROR_KIND_PERMISSION_DENIED,
            }
          if tool_context is not None and hasattr(
              tool_context, "request_confirmation"
          ):
            if is_computer_tool:
              tool_context.request_confirmation(
                  hint="Approve to allow browser automation for the requested computer-use action."
              )
            else:
              tool_context.request_confirmation(
                  hint=f"Approve to apply the requested mutation ({name})."
              )
            return {
              "error": "This tool call requires confirmation.",
              "error_kind": _ERROR_KIND_PERMISSION_BLOCK,
            }

        # Default behavior: user must re-run with --yes.
        if is_computer_tool:
          return {
            "error": (
              "Computer-use tools require confirmation: re-run with --yes to allow browser automation."
            ),
            "error_kind": _ERROR_KIND_PERMISSION_DENIED,
          }
        return {
          "error": (
            "Mutating tools require confirmation: re-run with --yes to allow "
            "write_file and search_replace."
          ),
          "error_kind": _ERROR_KIND_PERMISSION_DENIED,
        }
    if name in SHELL_TOOLS:
      if cfg.permission_mode == "strict":
        return {
          "error": "strict mode: shell tools disabled",
          "error_kind": _ERROR_KIND_PERMISSION_DENIED,
        }
      if not cfg.yes_to_all:
        if getattr(cfg, "interactive_permission_ask", False):
          if _hitl_sticky_enabled(tool_context):
            return None
          tc_state = _tool_confirmation_state(tool_context)
          if tc_state is True:
            _hitl_mark_session_approved(tool_context)
            _arm_shell_from_args(args)
            return None
          if tc_state is False:
            return {
              "error": "This tool call was rejected.",
              "error_kind": _ERROR_KIND_PERMISSION_DENIED,
            }
          if tool_context is not None and hasattr(tool_context, "request_confirmation"):
            cmd = args.get("command")
            cmd_args = args.get("args")
            hint = f"Approve to run command: {cmd} {cmd_args}" if cmd_args else f"Approve to run command: {cmd}"
            tool_context.request_confirmation(hint=hint)
            return {
              "error": "This tool call requires confirmation.",
              "error_kind": _ERROR_KIND_PERMISSION_BLOCK,
            }
        return {
          "error": (
            "Shell tools require confirmation: re-run with --yes or --interactive-ask to allow run_command."
          ),
          "error_kind": _ERROR_KIND_PERMISSION_DENIED,
        }
    return None

  return before_tool


def make_after_tool_callback(cfg: GemCodeConfig):
  """Track consecutive tool failures in session state (Claude-style circuit breaker)."""

  def after_tool(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context,
    tool_response: dict,
  ) -> dict | None:
    truncated = False
    if isinstance(tool_response, dict) and getattr(cfg, "tool_result_max_chars", 0) > 0:
      new_d, did = truncate_tool_result_dict(
          tool_response, int(cfg.tool_result_max_chars)
      )
      if did:
        tool_response = new_d
        truncated = True
    name = getattr(tool, "name", None) or ""
    if tool_context is None:
      return tool_response if truncated else None
    try:
      st = tool_context.state
    except Exception:
      return tool_response if truncated else None
    err = isinstance(tool_response, dict) and tool_response.get("error")
    err_kind = (
      isinstance(tool_response, dict) and tool_response.get("error_kind")
    )
    if err:
      # Only count failures that are actionable tool execution errors.
      # Permission denials are policy rejections (not "tool failures") and
      # should not trigger the circuit breaker.
      if err_kind not in (
        _ERROR_KIND_PERMISSION_DENIED,
        _ERROR_KIND_CIRCUIT_BREAKER,
        _ERROR_KIND_PERMISSION_BLOCK,
      ):
        st[_STATE_FAILURE_KEY] = st.get(_STATE_FAILURE_KEY, 0) + 1
        append_audit(
            cfg.project_root,
            {
              "phase": "tool_failure",
              "tool": name,
              "circuit": "fail",
              "streak": st[_STATE_FAILURE_KEY],
              "error_kind": err_kind,
            },
        )
      elif err_kind in (_ERROR_KIND_PERMISSION_DENIED, _ERROR_KIND_PERMISSION_BLOCK):
        # Policy denials shouldn't keep the failure streak alive.
        st[_STATE_FAILURE_KEY] = 0
    else:
      st[_STATE_FAILURE_KEY] = 0
    if _maybe_tool_summary_enabled():
      summary: dict[str, Any] = {
        "phase": "tool_result",
        "tool": name,
      }
      if isinstance(tool_response, dict) and tool_response.get("error"):
        summary["ok"] = False
        summary["error_kind"] = err_kind
        # Keep error string short; args already redacted in before_tool.
        e = tool_response.get("error")
        summary["error"] = str(e)[:2000] if e is not None else "unknown_error"
      else:
        summary["ok"] = True
        # Common lightweight metadata across our tools.
        for k in ("truncated", "total_bytes", "exit_code", "stdout", "stderr"):
          if isinstance(tool_response, dict) and k in tool_response:
            v = tool_response.get(k)
            if isinstance(v, str) and len(v) > 2000:
              summary[k] = v[:2000] + "[...]"
            else:
              summary[k] = v
      append_audit(cfg.project_root, summary)
      # Also print a concise, user-visible summary in CLI contexts.
      # (Claude Code renders tool cards; this is the lightweight equivalent.)
      try:
        # Full-screen TUIs get corrupted by stray stderr prints.
        if _truthy_env("GEMCODE_TUI_ACTIVE", default=False):
          return tool_response if truncated else None
        ok = bool(summary.get("ok"))
        prefix = "[tool ok]" if ok else "[tool err]"
        details = ""
        if isinstance(summary.get("exit_code"), int):
          details += f" exit={summary['exit_code']}"
        if not ok and summary.get("error_kind"):
          details += f" kind={summary.get('error_kind')}"
        if not ok and summary.get("error"):
          details += f" error={str(summary.get('error'))[:200]}"
        print(f"{prefix} {name}{details}", file=sys.stderr)
      except Exception:
        pass
    if truncated:
      return tool_response
    return None

  return after_tool


def _load_budget_tracker(st: Any) -> BudgetTracker:
  if st.get(_BT_T0, 0) in (0, None):
    bt = create_budget_tracker()
    st[_BT_T0] = bt.started_at_ms
    st[_BT_CC] = 0
    st[_BT_LD] = 0
    st[_BT_LG] = 0
    return bt
  return BudgetTracker(
      continuation_count=int(st.get(_BT_CC, 0)),
      last_delta_tokens=int(st.get(_BT_LD, 0)),
      last_global_turn_tokens=int(st.get(_BT_LG, 0)),
      started_at_ms=int(st.get(_BT_T0, 0)),
  )


def _save_budget_tracker(st: Any, bt: BudgetTracker) -> None:
  st[_BT_CC] = bt.continuation_count
  st[_BT_LD] = bt.last_delta_tokens
  st[_BT_LG] = bt.last_global_turn_tokens
  st[_BT_T0] = bt.started_at_ms


def make_after_model_callback(cfg: GemCodeConfig):
  """Log usage, accumulate session totals, optional token-budget audit (cf. cost-tracker)."""

  def after_model(callback_context, llm_response):
    um = getattr(llm_response, "usage_metadata", None)
    st = callback_context.state
    d: dict[str, Any] = {}
    if um is not None:
      for attr in (
          "prompt_token_count",
          "candidates_token_count",
          "cached_content_token_count",
          "total_token_count",
      ):
        if hasattr(um, attr):
          v = getattr(um, attr)
          if v is not None:
            d[attr] = v
    if d:
      append_audit(cfg.project_root, {"phase": "model_usage", **d})

    pt = d.get("prompt_token_count")
    if isinstance(pt, int) and pt >= 0:
      try:
        model_id = getattr(cfg, "model", "") or ""
        cw = calculate_context_warning_state(
            prompt_token_count=pt, model=model_id, cfg=cfg
        )
        level = worst_alert_level(cw)
        st[_LAST_PROMPT_TOKENS] = pt
        st[_LAST_CONTEXT_PCT] = cw.get("percent_left")
        st[_LAST_CONTEXT_LEVEL] = level
        append_audit(
            cfg.project_root,
            {
                "phase": "context_warning",
                "prompt_token_count": pt,
                "percent_left": cw.get("percent_left"),
                "level": level,
                "is_above_warning_threshold": cw.get("is_above_warning_threshold"),
                "is_above_error_threshold": cw.get("is_above_error_threshold"),
                "is_above_auto_compact_threshold": cw.get(
                    "is_above_auto_compact_threshold"
                ),
                "is_at_blocking_limit": cw.get("is_at_blocking_limit"),
            },
        )
        prev = int(st.get(_CTX_WARN_LEVEL_NOTIFIED, 0) or 0)
        if level < prev:
          st[_CTX_WARN_LEVEL_NOTIFIED] = level
          prev = level
        if (
            level > prev
            and not _truthy_env("GEMCODE_TUI_ACTIVE", default=False)
            and os.environ.get("GEMCODE_CONTEXT_WARNINGS", "1").lower()
            not in ("0", "false", "no", "off")
        ):
          labels = ("ok", "warning", "error", "blocking")
          label = labels[min(level, 3)]
          msg = (
              f"[gemcode context] ~{cw.get('percent_left')}% context left "
              f"(prompt_tokens≈{pt}; {label}). "
              "Use /compact or start a new session if you hit limits."
          )
          print(msg, file=sys.stderr)
          st[_CTX_WARN_LEVEL_NOTIFIED] = level
      except Exception:
        pass

    total_this = d.get("total_token_count")
    if isinstance(total_this, int) and total_this >= 0:
      prev_total = int(st.get(SESSION_TOTAL_TOKENS_KEY, 0) or 0)
      current_total = prev_total + total_this
      st[SESSION_TOTAL_TOKENS_KEY] = current_total

      base = st.get(_BT_BASE_TOTAL_TOKENS, -1)
      if base in (-1, None):
        st[_BT_BASE_TOTAL_TOKENS] = prev_total
        base = prev_total

      # Per-user-message token budget: tokens consumed since turn start.
      turn_tokens = int(current_total) - int(base)

      if cfg.token_budget and cfg.token_budget > 0:
        bt = _load_budget_tracker(st)
        decision = check_token_budget(bt, None, cfg.token_budget, turn_tokens)
        _save_budget_tracker(st, bt)
        if decision.action == "continue":
          # Make sure any prior stop is unset (defensive).
          st[_BT_TOKEN_BUDGET_STOP] = False
          append_audit(
              cfg.project_root,
              {
                "phase": "token_budget",
                "decision": "continue",
                "msg": decision.nudge_message,
              },
          )
        elif decision.action == "stop":
          st[_BT_TOKEN_BUDGET_STOP] = True
          if not st.get(TERMINAL_REASON_KEY):
            st[TERMINAL_REASON_KEY] = "token_budget_stop"
          if decision.completion_event:
            append_audit(
                cfg.project_root,
                {
                  "phase": "token_budget",
                  "decision": "stop",
                  **decision.completion_event,
                },
            )

    return None

  return after_model


def make_on_tool_error_callback(cfg: GemCodeConfig):
  """Turn tool exceptions into structured tool results (Claude-like is_error)."""

  async def on_tool_error(
    *, tool: BaseTool, args: dict[str, Any], tool_context, error: Exception
  ):
    name = getattr(tool, "name", None) or ""
    if tool_context is not None:
      try:
        st = tool_context.state
        if not st.get(TERMINAL_REASON_KEY):
          st[TERMINAL_REASON_KEY] = "tool_exception"
      except Exception:
        pass
    append_audit(
        cfg.project_root,
        {
          "phase": "tool_exception",
          "tool": name,
          "error": f"{type(error).__name__}: {error}",
          "args": _redact_args(name, args or {}),
        },
    )
    # Returning an error dict makes ADK proceed as a tool_result with is_error.
    return {
      "error": f"{type(error).__name__}: {error}",
      "error_kind": _ERROR_KIND_TOOL_EXCEPTION,
    }

  return on_tool_error


def make_on_model_error_callback(cfg: GemCodeConfig):
  """Structured model errors to the user + audit trail."""

  async def on_model_error(*, callback_context, llm_request, error: Exception):
    try:
      st = callback_context.state
      if st is not None and not st.get(TERMINAL_REASON_KEY):
        st[TERMINAL_REASON_KEY] = "model_error"
    except Exception:
      pass
    append_audit(
        cfg.project_root,
        {
            "phase": "model_exception",
            "error": f"{type(error).__name__}: {error}",
        },
    )
    if _truthy_env("GEMCODE_VERBOSE_MODEL_ERRORS", default=False):
      import traceback

      traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)

    user_text = format_model_error_for_user(error)
    # Scrollback/TUI already prints "GemCode:" before assistant text — avoid "GemCode: GemCode:".
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[
              types.Part(
                  text=(
                      f"{user_text} "
                      "You can re-run, shorten the message, or start a fresh session."
                  )
              )
            ],
        ),
        turn_complete=True,
    )

  return on_model_error
