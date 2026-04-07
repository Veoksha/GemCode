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
_RISK_FILES_TOUCHED = "gemcode:risk_files_touched"
_RISK_TOOL_CALLS = "gemcode:risk_tool_calls"

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

    # Dynamic risk signals from actual repo interaction.
    try:
      if tool_context is not None:
        st = tool_context.state
        st[_RISK_TOOL_CALLS] = int(st.get(_RISK_TOOL_CALLS, 0) or 0) + 1
        if name == "read_file":
          p = (args or {}).get("path")
          if isinstance(p, str) and p.strip():
            touched: set[str] = set(st.get(_RISK_FILES_TOUCHED, []) or [])
            touched.add(p.strip())
            # Store as list for JSON-serializable session state.
            st[_RISK_FILES_TOUCHED] = list(sorted(touched))[:200]
            # More files touched => higher complexity.
            n = len(touched)
            cur = float(getattr(cfg, "_risk_score", 0.0) or 0.0)
            if n >= 10:
              cur = min(1.0, cur + 0.08)
            elif n >= 5:
              cur = min(1.0, cur + 0.04)
            object.__setattr__(cfg, "_risk_score", cur)
        # Writes / shell are inherently higher risk; allow more evidence.
        if name in MUTATING_TOOLS:
          cur = float(getattr(cfg, "_risk_score", 0.0) or 0.0)
          object.__setattr__(cfg, "_risk_score", min(1.0, cur + 0.12))
        if name in SHELL_TOOLS:
          cur = float(getattr(cfg, "_risk_score", 0.0) or 0.0)
          object.__setattr__(cfg, "_risk_score", min(1.0, cur + 0.08))
    except Exception:
      pass

    # ── Shell hooks: pre_tool_use ─────────────────────────────────────────
    # If the project has a .gemcode/hooks/pre_tool_use.sh, run it now.
    # Non-zero exit or {"decision":"deny"} stdout will block the tool call.
    try:
      from gemcode.hooks import run_pre_tool_use_hook
      hook_result = run_pre_tool_use_hook(
          cfg.project_root,
          model=getattr(cfg, "model", "") or "",
          tool_name=name,
          args=args or {},
      )
      if hook_result is not None:
        return hook_result
    except Exception:
      pass

    # ── Permission rules (.gemcode/settings.json allow/deny) ──────────────
    # Evaluated before the normal permission flow so explicit allow/deny rules
    # override cfg.permission_mode and interactive prompts.
    try:
      from gemcode.permissions import check_rules
      rule_result = check_rules(name, args or {}, cfg.project_root)
      if rule_result == "deny":
        return {
          "error": f"Tool call blocked by .gemcode/settings.json deny rule for '{name}'.",
          "error_kind": _ERROR_KIND_PERMISSION_DENIED,
        }
      if rule_result == "allow":
        # Explicit allow — skip the normal permission prompt entirely
        # but still run post-tool hook (logging/audit).
        pass  # fall through to tool execution
    except Exception:
      rule_result = None

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

    # If permission rules explicitly allowed this tool call, skip the gate entirely.
    if rule_result == "allow" and (name in MUTATING_TOOLS or is_computer_tool):
      return None  # allow without prompting

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
    offloaded = False
    name = getattr(tool, "name", None) or ""

    # Offload oversized tool outputs to disk (stable refs) before truncation.
    # Dynamic caps for tool inline payload size.
    effective_tool_chars = int(getattr(cfg, "tool_result_max_chars", 0) or 0)
    try:
      from gemcode.dynamic_policy import get_dynamic_caps
      effective_tool_chars = get_dynamic_caps(cfg).tool_inline_chars
    except Exception:
      pass

    if (
      isinstance(tool_response, dict)
      and getattr(cfg, "tool_result_offload_enabled", False)
      and effective_tool_chars > 0
    ):
      try:
        from gemcode.tool_result_store import maybe_offload_tool_result
        new_payload, did = maybe_offload_tool_result(
          project_root=cfg.project_root,
          tool_name=name,
          payload=tool_response,
          max_inline_chars=int(effective_tool_chars),
        )
        if did and isinstance(new_payload, dict):
          tool_response = new_payload
          offloaded = True
      except Exception:
        pass

    if isinstance(tool_response, dict) and effective_tool_chars > 0:
      new_d, did = truncate_tool_result_dict(
          tool_response, int(effective_tool_chars)
      )
      if did:
        tool_response = new_d
        truncated = True
    if tool_context is None:
      return tool_response if (truncated or offloaded) else None
    try:
      st = tool_context.state
    except Exception:
      return tool_response if (truncated or offloaded) else None
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

    # Risk feedback: if tools are failing or commands return non-zero, treat the
    # task as higher-risk and allow more evidence in subsequent tool outputs.
    try:
      cur = float(getattr(cfg, "_risk_score", 0.0) or 0.0)
      bump = 0.0
      if err:
        bump += 0.15
      if isinstance(tool_response, dict) and isinstance(tool_response.get("exit_code"), int):
        if int(tool_response["exit_code"]) != 0:
          bump += 0.10
          # Test/build failures should boost evidence allowance more.
          if name in ("bash", "run_command"):
            bump += 0.05
      # decay slowly when things are healthy
      if bump == 0.0:
        cur = max(0.0, cur * 0.90)
      else:
        cur = min(1.0, cur + bump)
      object.__setattr__(cfg, "_risk_score", cur)
    except Exception:
      pass
    # ── Shell hooks: post_tool_use ────────────────────────────────────────
    try:
      from gemcode.hooks import run_post_tool_use_hook
      run_post_tool_use_hook(
          cfg.project_root,
          model=getattr(cfg, "model", "") or "",
          tool_name=name,
          args=args or {},
          result=tool_response if isinstance(tool_response, dict) else {},
      )
    except Exception:
      pass

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
    if offloaded:
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
          "thoughts_token_count",
      ):
        if hasattr(um, attr):
          v = getattr(um, attr)
          if v is not None:
            d[attr] = v
    if d:
      append_audit(cfg.project_root, {"phase": "model_usage", **d})

    # ── Expose live token stats to the TUI ───────────────────────────────────
    # The TUI reads cfg._last_turn_stats after each turn to display token counts
    # and estimated cost in the footer (like OpenClaude's spinner token display).
    try:
      in_tok  = d.get("prompt_token_count", 0) or 0
      out_tok = d.get("candidates_token_count", 0) or 0
      think_tok = d.get("thoughts_token_count", 0) or 0
      cache_tok = d.get("cached_content_token_count", 0) or 0
      total_tok = d.get("total_token_count", 0) or 0

      prev_session_tokens = int(st.get(SESSION_TOTAL_TOKENS_KEY, 0) or 0)
      session_total = prev_session_tokens + total_tok

      from gemcode.pricing import estimate_cost
      turn_cost = estimate_cost(
          getattr(cfg, "model", "") or "",
          input_tokens=in_tok,
          output_tokens=out_tok,
      )
      # Accumulate session cost
      prev_cost = getattr(cfg, "_session_cost_usd", 0.0) or 0.0
      session_cost = prev_cost + (turn_cost or 0.0)
      object.__setattr__(cfg, "_session_cost_usd", session_cost)

      stats: dict[str, Any] = {
          "in": in_tok,
          "out": out_tok,
          "think": think_tok,
          "cache": cache_tok,
          "total": total_tok,
          "session_total": session_total,
          "turn_cost": turn_cost,
          "session_cost": session_cost,
      }
      object.__setattr__(cfg, "_last_turn_stats", stats)
    except Exception:
      pass

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
        # Expose to tool layer (dynamic token policy).
        try:
          pct = cw.get("percent_left")
          if isinstance(pct, int):
            object.__setattr__(cfg, "_context_percent_left", pct)
          object.__setattr__(cfg, "_context_alert_level", int(level))
        except Exception:
          pass
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
  """Structured model errors to the user + audit trail.

  For transient errors (HTTP 503, 429, server-overloaded) we return None so the
  exception propagates to invoke.py, which retries with exponential backoff.
  For permanent errors we absorb and return a user-friendly LlmResponse.
  """

  async def on_model_error(*, callback_context, llm_request, error: Exception):
    from gemcode.model_errors import is_transient_error

    append_audit(
        cfg.project_root,
        {
            "phase": "model_exception",
            "error": f"{type(error).__name__}: {error}",
            "transient": is_transient_error(error),
        },
    )

    # Transient errors (503, 429, server-overloaded): let the exception propagate
    # so invoke.py can retry with backoff. Do NOT set terminal state here — the
    # turn is not over yet.
    if is_transient_error(error):
      return None

    # Permanent errors: mark session terminal and return a user-friendly message.
    try:
      st = callback_context.state
      if st is not None and not st.get(TERMINAL_REASON_KEY):
        st[TERMINAL_REASON_KEY] = "model_error"
    except Exception:
      pass

    if _truthy_env("GEMCODE_VERBOSE_MODEL_ERRORS", default=False):
      import traceback
      traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)

    user_text = format_model_error_for_user(error)
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
