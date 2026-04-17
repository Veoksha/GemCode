"""
Single user turn (inner path ≈ `query()` invocation per message).

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


# Delays (seconds) between successive transient-error retries: 2s, 5s, 12s.
# Three retries = up to ~19 seconds of total wait before giving up.
_TRANSIENT_RETRY_DELAYS = [2.0, 5.0, 12.0]


_HITL_PROMPT_LOCK = Lock()

async def _maybe_enqueue_kaira_autopilot(*, cfg: "GemCodeConfig", session_id: str) -> None:
  """If Kaira IPC is available, enqueue background quality checks after edits."""
  try:
    enabled = os.environ.get("GEMCODE_KAIRA_AUTOPILOT", "1").strip().lower() in (
      "1",
      "true",
      "yes",
      "on",
    )
    if not enabled:
      return
    # Only run when files were touched (edit tools track this).
    touched = getattr(cfg, "_touched_paths", None)
    if not touched:
      return
    # Avoid enqueuing repeatedly within the same session unless touched paths change.
    last_fp = str(getattr(cfg, "_kaira_autopilot_fp", "") or "")
    fp = ",".join(sorted(str(x) for x in touched))[:4000]
    if fp and fp == last_fp:
      return
    object.__setattr__(cfg, "_kaira_autopilot_fp", fp)

    sock = os.environ.get("GEMCODE_KAIRA_SOCKET") or str(cfg.project_root / ".gemcode" / "ipc.sock")
    if not Path(sock).exists():
      return
    # Heuristic: suggest likely checks, but let the agent choose based on repo.
    prompt = (
      "You are Kaira (background worker). Run the most relevant automated quality checks for this repo "
      "based on its files, and report succinctly.\n\n"
      "Rules:\n"
      "- Prefer fast checks first (lint/typecheck/unit tests).\n"
      "- If Python: try `pytest -q` (or detect other common runners).\n"
      "- If Node: try `npm test` / `npm run lint` when package.json exists.\n"
      "- If there are failures, include the smallest actionable summary and exact command to reproduce.\n"
      "- If everything passes, say PASS and list what you ran.\n"
      "- Return a final STRICT JSON report with keys: status, summary, evidence, recommended_next_actions.\n\n"
      f"Touched files (recent): {', '.join(sorted(list(touched))[:30])}"
    )

    # Org-aware routing: delegate to the kaira org member if present so the
    # manager/worker hierarchy stays consistent.
    try:
      auto_org = os.environ.get("GEMCODE_AUTO_SLASH_ORG", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
      )
      if auto_org:
        from gemcode.org import find_member
        m = find_member(cfg.project_root, "kaira")
        if m is not None and m.kind == "kaira_worker":
          from gemcode.tools.org_tools import make_org_tools
          tools = make_org_tools(cfg)
          org_delegate = None
          for t in tools:
            if getattr(t, "__name__", "") == "org_delegate":
              org_delegate = t
              break
          if org_delegate is not None:
            await org_delegate("kaira", prompt, "")  # type: ignore[misc]
            return
    except Exception:
      pass

    from gemcode.kaira_client import KairaIpcClient

    client = await KairaIpcClient.connect(socket_path=sock)
    try:
      # Low priority by default; user can override with env in future.
      await client.request(action="enqueue", prompt=prompt, priority=-1, session_id=session_id)
    finally:
      await client.close()
  except Exception:
    return


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
    attachment_paths: Sequence[Path | str] | None = None,
) -> list:
  """Execute one user message; collect all Events (caller aggregates text)."""
  # Dynamic risk score: updated each user message; later refined by tool outcomes.
  # This is intentionally heuristic but configurable via env knobs.
  if cfg is not None:
    try:
      object.__setattr__(cfg, "_active_session_id", session_id)
    except Exception:
      pass
    try:
      import re
      p = (prompt or "")[:20_000]
      risk = 0.0
      # Complexity signals
      if len(p) > 600:
        risk += 0.15
      if len(p) > 2000:
        risk += 0.15
      if re.search(r"\\b(refactor|migrate|rewrite|optimi[sz]e|architecture)\\b", p, re.I):
        risk += 0.2
      if re.search(r"\\b(bug|fix|regression|error|traceback|failing)\\b", p, re.I):
        risk += 0.2
      if re.search(r"\\b(test|pytest|ci|build|deploy|release)\\b", p, re.I):
        risk += 0.1
      if attachment_paths:
        risk = min(1.0, risk + 0.12)
      # Multi-file hints
      if p.count("/") >= 6 or p.count(".py") + p.count(".ts") + p.count(".tsx") >= 3:
        risk += 0.1
      # Clamp 0..1
      risk = max(0.0, min(1.0, float(risk)))
      object.__setattr__(cfg, "_risk_score", risk)
    except Exception:
      pass

    # Deterministic manager dispatcher: pre-delegate certain work to org members
    # and inject their results into the prompt before the main agent runs.
    try:
      auto_mgr = os.environ.get("GEMCODE_MANAGER_DISPATCH", "1").strip().lower() in (
        "1","true","yes","on"
      )
      if auto_mgr:
        import re
        p0 = (prompt or "")[:12_000]
        # Only do this for broad/complex prompts.
        score = float(getattr(cfg, "_parallelism_score", 0.0) or 0.0)
        if score >= float(os.environ.get("GEMCODE_MANAGER_DISPATCH_THRESHOLD", "0.7")):
          from gemcode.tools.org_tools import make_org_tools
          tools = make_org_tools(cfg)
          org_delegate = None
          for t in tools:
            if getattr(t, "__name__", "") == "org_delegate":
              org_delegate = t
              break
          # Ask verifier for a quick risk check / plan critique (fast, in-process).
          if org_delegate is not None and re.search(r"\\b(fix|refactor|rewrite|change|implement)\\b", p0, re.I):
            v = await org_delegate("verifier", "Review the requested task and list key risks + verification steps.", p0)  # type: ignore[misc]
            if isinstance(v, dict) and v.get("ok") and v.get("result"):
              object.__setattr__(cfg, "_manager_verifier_context", str(v.get("result"))[:4000])
    except Exception:
      pass
    # Parallelism score: heuristic signal for automatic subtask fan-out.
    try:
      import re
      p2 = (prompt or "")[:20_000]
      par = 0.0
      if len(p2) > 800:
        par += 0.15
      if len(p2) > 2400:
        par += 0.2
      if re.search(r"\b(analy[sz]e|audit|map|inventory|scan|survey)\b", p2, re.I):
        par += 0.2
      if re.search(r"\b(refactor|migrate|rewrite|redesign|architecture)\b", p2, re.I):
        par += 0.2
      if re.search(r"\b(across|entire|whole|end-to-end|e2e|multiple modules|many files)\b", p2, re.I):
        par += 0.2
      if p2.count("/") >= 8 or p2.count(".py") + p2.count(".ts") + p2.count(".tsx") >= 5:
        par += 0.15
      if attachment_paths:
        par += 0.08
      par = max(0.0, min(1.0, float(par)))
      object.__setattr__(cfg, "_parallelism_score", par)
    except Exception:
      pass
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

      # First message: optional inline files + text (Gemini multimodal).
      if attachment_paths:
        from gemcode.multimodal_input import build_user_content

        # Optional HITL gate for local attachment reads/upload materialization.
        # This is separate from workspace trust: the OS may still require
        # user-granted "Files and Folders" permissions on first access.
        # If approved once, we don't re-prompt for the rest of this session.
        attach_allow = True
        if hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
          # Default-on: attachments can read any local file path (not workspace-scoped),
          # but we ask once per session so the user is in control and macOS can trigger
          # its permission prompt at the moment we attempt the read.
          attach_allow = os.environ.get("GEMCODE_ATTACHMENTS_ASK", "1").lower() not in (
            "0",
            "false",
            "no",
            "off",
          )
          if cfg is not None:
            # If user already approved earlier in this session, don't prompt again.
            if bool(getattr(cfg, "_attachments_allowed", False)):
              attach_allow = True
            # If yes-to-all is enabled, auto-allow attachments.
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
          # Non-interactive sessions can't prompt; default to allow.
          attach_allow = True
        effective_attachments = attachment_paths if attach_allow else None

        root = cfg.project_root if cfg is not None else Path.cwd()
        current_message, attach_warn = build_user_content(
            prompt,
            effective_attachments,
            project_root=root,
        )
        for w in attach_warn:
          print(f"[gemcode] {w}", file=sys.stderr)
      else:
        effective_prompt = prompt
        # Auto fan-out: when prompt looks broad, guide model to spawn parallel
        # isolated subtasks first (bounded) and then synthesise.
        try:
          auto_on = os.environ.get("GEMCODE_AUTO_FANOUT", "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
          )
          threshold = float(os.environ.get("GEMCODE_AUTO_FANOUT_THRESHOLD", "0.6"))
          if auto_on and cfg is not None:
            score = float(getattr(cfg, "_parallelism_score", 0.0) or 0.0)
            already = bool(getattr(cfg, "_auto_fanout_applied", False))
            if (not already) and score >= threshold:
              object.__setattr__(cfg, "_auto_fanout_applied", True)
              effective_prompt = (
                "Before you answer, do a quick parallel exploration pass:\n"
                "- Decompose this into 3–6 independent investigation subtasks.\n"
                "- Call `spawn_subtasks(tasks=[...], max_concurrency=4)` to run them in parallel.\n"
                "- If an org member is appropriate, prefer delegating with `org_delegate` / `org_spawn`.\n"
                "- Synthesize the results into a single plan/answer, then proceed.\n\n"
                + (prompt or "")
              )
        except Exception:
          pass

        # Inject dispatcher context (e.g., verifier critique) if available.
        try:
          if cfg is not None:
            ctx = str(getattr(cfg, "_manager_verifier_context", "") or "").strip()
            if ctx:
              effective_prompt = (
                "Manager pre-delegation result (verifier):\n"
                + ctx
                + "\n\nUser request:\n"
                + (effective_prompt or "")
              )
        except Exception:
          pass

        current_message = types.Content(role="user", parts=[types.Part(text=effective_prompt)])

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
        auto_ok = bool(
          cfg is not None
          and (
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

      # Background autopilot: if we touched files, enqueue Kaira checks (best-effort).
      if cfg is not None:
        try:
          await _maybe_enqueue_kaira_autopilot(cfg=cfg, session_id=session_id)
        except Exception:
          pass
      return collected
  finally:
    if cfg is not None and orig_ctx_chars is not None:
      cfg.max_context_chars = orig_ctx_chars
    if cfg is not None and orig_tool_chars is not None:
      cfg.tool_result_max_chars = orig_tool_chars
