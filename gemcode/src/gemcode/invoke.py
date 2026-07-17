"""
Single user turn (inner path).

CLI and tests call `run_turn` with a Runner already bound to app + session service.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from threading import Lock
from typing import Any, Sequence

from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.genai import types


_HITL_PROMPT_LOCK = Lock()

# ── Cached regex (compiled once, not per-call) ────────────────────────────────
import re as _re
_RISK_COMPLEX = _re.compile(r"\b(refactor|migrate|rewrite|optimi[sz]e|architecture)\b", _re.I)
_RISK_BUG = _re.compile(r"\b(bug|fix|regression|error|traceback|failing)\b", _re.I)
_RISK_TEST = _re.compile(r"\b(test|pytest|ci|build|deploy|release)\b", _re.I)


def _events_to_text(events: list[Any]) -> str:
  """Best-effort extraction of assistant text from ADK events."""
  parts: list[str] = []
  for event in events:
    try:
      content = getattr(event, "content", None)
      if not content or not getattr(content, "parts", None):
        continue
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
  if "request may be too large" in t:
    return True
  if "gemcode_max_context_chars" in t or "gemcode_tool_result_max_chars" in t:
    return True
  if "context" in t and ("too large" in t or "token" in t or "length" in t):
    return True
  return False


def _compute_risk_score(prompt: str, attachment_paths: Sequence | None) -> float:
  """Fast risk scoring with pre-compiled regex."""
  p = (prompt or "")[:20_000]
  risk = 0.0
  if len(p) > 600:
    risk += 0.15
  if len(p) > 2000:
    risk += 0.15
  if _RISK_COMPLEX.search(p):
    risk += 0.2
  if _RISK_BUG.search(p):
    risk += 0.2
  if _RISK_TEST.search(p):
    risk += 0.1
  if attachment_paths:
    risk += 0.12
  if p.count("/") >= 6 or p.count(".py") + p.count(".ts") + p.count(".tsx") >= 3:
    risk += 0.1
  return max(0.0, min(1.0, risk))


def _is_simple_prompt(prompt: str) -> bool:
  """Detect simple prompts that don't need heavy orchestration overhead."""
  p = (prompt or "").strip()
  # Short prompts (< 100 chars) with no tool-triggering keywords
  if len(p) < 100 and "/" not in p and "file" not in p.lower():
    return True
  # Greetings and simple questions
  if len(p) < 50:
    return True
  return False


def _is_simple_prompt_for_cfg(cfg: GemCodeConfig, prompt: str, attachment_paths) -> bool:
  """Code/Agents mode in the web UI always gets full workspace enrichment."""
  if getattr(cfg, "_web_workspace_mode", None) in ("code", "agents"):
    return False
  if attachment_paths:
    return False
  return _is_simple_prompt(prompt)


def prepare_turn_prompt(
    cfg: "GemCodeConfig",
    prompt: str,
    *,
    session_id: str,
    attachment_paths: Sequence[Path | str] | None = None,
    consume_fleet_reports: bool = True,
) -> str:
  """Apply the same prompt enrichment as ``run_turn`` before invoking the runner."""
  is_simple = _is_simple_prompt_for_cfg(cfg, prompt, attachment_paths)

  if not is_simple:
    try:
      from gemcode.agent_mesh import ensure_mesh
      mesh = ensure_mesh(cfg)
      mesh.start()
    except Exception:
      pass

    try:
      if not getattr(cfg, "_intelligence_bootstrapped", False):
        object.__setattr__(cfg, "_intelligence_bootstrapped", True)
        from gemcode.agent_intelligence import first_session_bootstrap
        first_session_bootstrap(cfg)
    except Exception:
      pass

  try:
    object.__setattr__(cfg, "_active_session_id", session_id)
  except Exception:
    pass

  if consume_fleet_reports and not is_simple:
    try:
      from gemcode.fleet_reports import drain_for_prompt
      preamble = drain_for_prompt(cfg.project_root)
      if preamble:
        prompt = preamble + "\n\n---\n\n" + (prompt or "")
    except Exception:
      pass

  if not is_simple:
    try:
      from gemcode.agent_intelligence import enhance_turn
      prompt = enhance_turn(cfg, prompt)
    except Exception:
      pass

    try:
      from gemcode.codebase_awareness import build_awareness_context
      if getattr(cfg, "_web_workspace_mode", None) != "chat":
        awareness = build_awareness_context(cfg.project_root)
        if awareness:
          prompt = awareness + "\n\n" + prompt
    except Exception:
      pass

  try:
    object.__setattr__(cfg, "_risk_score", _compute_risk_score(prompt, attachment_paths))
  except Exception:
    pass

  return prompt


async def run_turn(
    runner: Runner,
    *,
    user_id: str,
    session_id: str,
    prompt: str,
    max_llm_calls: int | None = None,
    cfg: "GemCodeConfig | None" = None,
    attachment_paths: Sequence[Path | str] | None = None,
    consume_fleet_reports: bool = True,
) -> list:
  """Execute one user message; collect all Events (caller aggregates text)."""

  # ── Fast path: skip heavy machinery for simple prompts ──────────────────
  is_simple = _is_simple_prompt_for_cfg(cfg, prompt, attachment_paths) if cfg is not None else (
    _is_simple_prompt(prompt) and not attachment_paths
  )

  # ── Mesh + bootstrap (only on first call or complex prompts) ────────────
  if cfg is not None and not is_simple:
    try:
      from gemcode.agent_mesh import ensure_mesh
      mesh = ensure_mesh(cfg)
      mesh.start()  # Starts background thread if not already running
    except Exception:
      pass

    # First-session bootstrap (once per session)
    try:
      if not getattr(cfg, "_intelligence_bootstrapped", False):
        object.__setattr__(cfg, "_intelligence_bootstrapped", True)
        from gemcode.agent_intelligence import first_session_bootstrap
        first_session_bootstrap(cfg)
    except Exception:
      pass

  if cfg is not None:
    try:
      object.__setattr__(cfg, "_active_session_id", session_id)
    except Exception:
      pass

    # Drain fleet reports into prompt (skip for simple prompts — no background work expected)
    if consume_fleet_reports and not is_simple:
      try:
        from gemcode.fleet_reports import drain_for_prompt
        preamble = drain_for_prompt(cfg.project_root)
        if preamble:
          prompt = preamble + "\n\n---\n\n" + (prompt or "")
      except Exception:
        pass

    # Intelligence layer (skip for simple prompts)
    if not is_simple:
      try:
        from gemcode.agent_intelligence import enhance_turn
        prompt = enhance_turn(cfg, prompt)
      except Exception:
        pass

      # Codebase awareness: inject persistent project understanding
      try:
        from gemcode.codebase_awareness import build_awareness_context
        if getattr(cfg, "_web_workspace_mode", None) != "chat":
          awareness = build_awareness_context(cfg.project_root)
          if awareness:
            prompt = awareness + "\n\n" + prompt
      except Exception:
        pass

    # Risk score (always compute — it's fast with pre-compiled regex)
    try:
      object.__setattr__(cfg, "_risk_score", _compute_risk_score(prompt, attachment_paths))
    except Exception:
      pass

  run_config = (
    RunConfig(max_llm_calls=max_llm_calls) if max_llm_calls is not None else None
  )

  REQUEST_CONFIRMATION_FC = "adk_request_confirmation"

  def _get_confirmation_requests(events: list) -> list[types.FunctionCall]:
    """Return confirmation FCs from the last event in the batch that has any."""
    for ev in reversed(events):
      try:
        fcs = [
          fc for fc in (ev.get_function_calls() or [])
          if getattr(fc, "name", None) == REQUEST_CONFIRMATION_FC
        ]
        if fcs:
          return fcs
      except Exception:
        continue
    return []

  def _extract_hint_and_tool(fc: types.FunctionCall) -> tuple[str, str]:
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
    "1", "true", "yes", "on",
  )
  retry_max_attempts = int(os.environ.get("GEMCODE_MODEL_ERROR_RETRY_MAX_ATTEMPTS", "2"))
  retry_shrink_factor = float(os.environ.get("GEMCODE_MODEL_ERROR_RETRY_SHRINK_FACTOR", "0.6"))
  if retry_max_attempts < 2:
    retry_enabled = False

  orig_ctx_chars: int | None = None
  orig_tool_chars: int | None = None

  try:
    for attempt in range(retry_max_attempts):
      collected: list = []

      state_delta = None
      if cfg is not None and cfg.token_budget:
        from gemcode.config import token_budget_invocation_reset
        state_delta = token_budget_invocation_reset()

      # Build user message
      if attachment_paths:
        from gemcode.multimodal_input import build_user_content

        attach_allow = True
        if hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
          attach_allow = os.environ.get("GEMCODE_ATTACHMENTS_ASK", "1").lower() not in (
            "0", "false", "no", "off",
          )
          if cfg is not None:
            if bool(getattr(cfg, "_attachments_allowed", False)):
              attach_allow = True
            elif bool(getattr(cfg, "yes_to_all", False)):
              attach_allow = True
              object.__setattr__(cfg, "_attachments_allowed", True)
            elif attach_allow:
              attach_allow = _prompt_yes_no(
                "Allow GemCode to read and upload the attached file(s) from disk? (y/n) "
              )
              if attach_allow:
                object.__setattr__(cfg, "_attachments_allowed", True)
        else:
          attach_allow = True

        root = cfg.project_root if cfg is not None else Path.cwd()
        current_message, attach_warn = build_user_content(
            prompt, attachment_paths if attach_allow else None, project_root=root,
        )
        for w in attach_warn:
          print(f"[gemcode] {w}", file=sys.stderr)
      else:
        current_message = types.Content(role="user", parts=[types.Part(text=prompt)])

      # ── Runner execution loop ───────────────────────────────────────────
      async def _await_runner_events(*, next_message: types.Content, do_reset: bool):
        kwargs = dict(user_id=user_id, session_id=session_id, new_message=next_message)
        if run_config is not None:
          kwargs["run_config"] = run_config
        if do_reset and state_delta is not None:
          kwargs["state_delta"] = state_delta
        events: list = []
        async for event in runner.run_async(**kwargs):
          events.append(event)
        return events

      do_reset = True
      transient_attempts = 0
      while True:
        try:
          events = await _await_runner_events(next_message=current_message, do_reset=do_reset)
        except Exception as _exc:
          from gemcode.model_errors import API_TRANSIENT_RETRY_DELAYS_SEC, is_transient_error
          if is_transient_error(_exc) and transient_attempts < len(API_TRANSIENT_RETRY_DELAYS_SEC):
            delay = API_TRANSIENT_RETRY_DELAYS_SEC[transient_attempts]
            transient_attempts += 1
            _msg = (
              f"\n[gemcode] Transient API error ({type(_exc).__name__}). "
              f"Retrying in {delay:.0f}s (attempt {transient_attempts}/{len(API_TRANSIENT_RETRY_DELAYS_SEC)})...\n"
            )
            print(_msg, file=sys.stderr)
            if os.environ.get("GEMCODE_TUI_ACTIVE", "0").lower() in ("1", "true", "yes", "on"):
              try:
                from gemcode.tui import scrollback as _sb
                _sb._transient_retry_notice = _msg
              except Exception:
                pass
            await asyncio.sleep(delay)
            continue
          raise

        transient_attempts = 0
        collected.extend(events)

        confirmation_fcs = _get_confirmation_requests(events)
        if not confirmation_fcs:
          break

        # HITL confirmation handling
        interactive_enabled = bool(
          getattr(cfg, "interactive_permission_ask", False)
          and hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
        )
        auto_ok = bool(
          cfg is not None and (
            bool(getattr(cfg, "yes_to_all", False))
            or bool(getattr(cfg, "super_mode", False))
          )
        )

        parts: list[types.Part] = []
        for fc in confirmation_fcs:
          tool_name, hint = _extract_hint_and_tool(fc)
          if auto_ok:
            ok = True
          elif interactive_enabled:
            suffix = f"\n  Hint: {hint}" if hint else ""
            ok = _prompt_yes_no(f"\n[gemcode HITL] Approve tool call '{tool_name}'? [y/N]{suffix}\n> ")
          else:
            ok = False
            print(f"[gemcode HITL] Tool confirmation for '{tool_name}' auto-rejected (non-interactive).", file=sys.stderr)

          parts.append(types.Part(
            function_response=types.FunctionResponse(
              name=REQUEST_CONFIRMATION_FC, id=getattr(fc, "id", None), response={"confirmed": ok},
            )
          ))

        current_message = types.Content(role="user", parts=parts)
        do_reset = False

      # Retry on context overflow
      if (
        attempt == 0 and retry_enabled and cfg is not None
        and hasattr(cfg, "max_context_chars") and hasattr(cfg, "tool_result_max_chars")
        and attempt + 1 < retry_max_attempts
      ):
        assistant_text = _events_to_text(collected)
        if _is_retryable_context_model_error(assistant_text):
          orig_ctx_chars = cfg.max_context_chars
          orig_tool_chars = cfg.tool_result_max_chars
          cfg.max_context_chars = max(50_000, int(orig_ctx_chars * retry_shrink_factor))
          cfg.tool_result_max_chars = max(1_000, int(orig_tool_chars * retry_shrink_factor))
          continue

      # Post-turn learning (async-safe, non-blocking for simple prompts)
      if cfg is not None and not is_simple:
        try:
          from gemcode.agent_intelligence import post_turn_learn
          post_turn_learn(cfg, collected)
        except Exception:
          pass

      return collected
  finally:
    if cfg is not None and orig_ctx_chars is not None:
      cfg.max_context_chars = orig_ctx_chars
    if cfg is not None and orig_tool_chars is not None:
      cfg.tool_result_max_chars = orig_tool_chars
