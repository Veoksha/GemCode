from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from gemcode.config import GemCodeConfig, load_cli_environment
from gemcode.session_runtime import create_runner
from gemcode.tui.scrollback import format_tool_call_extras

from gemcode.tool_registry import MUTATING_TOOLS, READ_ONLY_TOOLS, SHELL_TOOLS

REQUEST_CONFIRMATION_FC = "adk_request_confirmation"

_WEB_SEARCH_TOOLS = frozenset(
  {"google_search", "web_search", "search_web", "web_fetch", "google_maps_grounding"}
)


class _WebIdeEmitter:
  """Forward IDE proposal events into the web SSE stream."""

  def send(self, msg: dict) -> None:
    m = dict(msg or {})
    t = str(m.get("type") or "")
    if t in ("edit_proposal", "command_suggestion"):
      _sse_emit(m)


def extract_text_from_event(event: Any) -> str:
  """Assistant-visible final text only (excludes model thought parts)."""
  final, _thought = extract_parts_from_event(event)
  return final


def extract_parts_from_event(event: Any) -> tuple[str, str]:
  """Return (final_text, thought_text) from one ADK event."""
  try:
    content = getattr(event, "content", None)
    author = getattr(event, "author", None)
    if author == "user":
      return "", ""
    if not content or not getattr(content, "parts", None):
      return "", ""
    final_parts: list[str] = []
    thought_parts: list[str] = []
    for p in content.parts:
      t = getattr(p, "text", None)
      if not isinstance(t, str) or not t:
        continue
      if getattr(p, "thought", None):
        thought_parts.append(t)
      else:
        final_parts.append(t)
    return "".join(final_parts), "".join(thought_parts)
  except Exception:
    return "", ""


def _extract_text_from_message_content(content: Any) -> str:
  if isinstance(content, str):
    return content
  if isinstance(content, list):
    parts: list[str] = []
    for block in content:
      if isinstance(block, dict) and block.get("type") == "text":
        text = block.get("text")
        if isinstance(text, str) and text:
          parts.append(text)
    if parts:
      return "\n".join(parts)
  try:
    return json.dumps(content, ensure_ascii=False)
  except Exception:
    return str(content)


def _latest_user_message(messages: list[dict[str, Any]]) -> str:
  for m in reversed(messages):
    if m.get("role") == "user":
      return _extract_text_from_message_content(m.get("content"))
  return ""


def _build_prompt(messages: list[dict[str, Any]]) -> str:
  """Fallback when no stable session id — embed history in one prompt."""
  lines: list[str] = []
  for m in messages:
    role = m.get("role")
    content = _extract_text_from_message_content(m.get("content"))
    if role == "user":
      lines.append(f"User: {content}")
    elif role == "assistant":
      lines.append(f"Assistant: {content}")
  if not lines:
    return ""
  return "Conversation so far:\n" + "\n".join(lines) + "\n\nNow respond as the assistant."


def _sse_emit(obj: dict[str, Any]) -> None:
  sys.stdout.write(f"data: {json.dumps(obj)}\n\n")
  sys.stdout.flush()


def _sse_keepalive(*, message: str = "Still working…", elapsed_s: float | None = None) -> None:
  payload: dict[str, Any] = {"type": "status", "phase": "working", "message": message}
  if elapsed_s is not None:
    payload["elapsed_s"] = elapsed_s
  _sse_emit(payload)


def _iter_chunks(text: str, chunk_size: int) -> Iterable[str]:
  if chunk_size <= 0:
    yield text
    return
  for i in range(0, len(text), chunk_size):
    yield text[i : i + chunk_size]


async def _emit_text_delta(index: int, delta: str) -> None:
  if not delta:
    return
  chunk_size = int(os.environ.get("GEMCODE_WEB_STREAM_CHUNK", "8"))
  for piece in _iter_chunks(delta, max(1, chunk_size)):
    _sse_emit(
      {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "text_delta", "text": piece},
      }
    )
    _sse_emit({"type": "text", "content": piece})
    await asyncio.sleep(0)


async def _emit_thinking_delta(delta: str) -> None:
  if not delta:
    return
  _sse_emit({"type": "thinking", "content": delta})


def _emit_status(phase: str, *, message: str = "", elapsed_s: float | None = None) -> None:
  payload: dict[str, Any] = {"type": "status", "phase": phase}
  if message:
    payload["message"] = message
  if elapsed_s is not None:
    payload["elapsed_s"] = round(elapsed_s, 1)
  _sse_emit(payload)


def _fmt_tool_summary(response: Any) -> str:
  """One-line tool result summary (mirrors TUI scrollback)."""
  try:
    d = response if isinstance(response, dict) else {}
    inner = d.get("result", d)
    if not isinstance(inner, dict):
      inner = d
    err = inner.get("error") or d.get("error")
    if err:
      return f"✗ {str(err)[:80]}"
    exit_code = inner.get("exit_code")
    if exit_code is not None:
      icon = "✓" if exit_code == 0 else f"✗ exit {exit_code}"
      out = str(inner.get("stdout", "") or "").strip()
      first = out.split("\n")[0][:80] if out else ""
      if not first:
        first = str(inner.get("stderr", "") or "").strip().split("\n")[0][:80]
      return f"{icon}  {first}" if first else icon
    if inner.get("content") is not None:
      lines = str(inner["content"]).count("\n") + 1
      return f"✓ {lines} lines"
    if inner.get("files") is not None:
      return f"✓ {len(inner['files'])} files"
    if inner.get("matches") is not None:
      return f"✓ {len(inner['matches'])} matches"
    if inner.get("ok") or d.get("ok"):
      return "✓"
    return ""
  except Exception:
    return ""


def _tool_result_text(response: Any) -> tuple[str, bool, str]:
  summary = _fmt_tool_summary(response)
  try:
    d = response if isinstance(response, dict) else {}
    inner = d.get("result", d)
    if not isinstance(inner, dict):
      inner = d
    err = inner.get("error") or d.get("error")
    if err:
      return str(err)[:4000], True, summary or f"✗ {str(err)[:80]}"
    if inner.get("content") is not None:
      return str(inner["content"])[:4000], False, summary or "✓"
    if inner.get("stdout") is not None:
      text = str(inner.get("stdout", ""))[:4000]
      is_err = bool(inner.get("exit_code"))
      return text, is_err, summary or ("✓" if not is_err else "✗")
    text = json.dumps(d, ensure_ascii=False)[:4000]
    return text, False, summary
  except Exception:
    return str(response)[:4000], False, summary


def _emit_tool_calls(event: Any) -> bool:
  """Emit tool_use frames. Returns True if any real tool call was emitted."""
  emitted = False
  try:
    for fc in event.get_function_calls() or []:
      name = getattr(fc, "name", "") or ""
      if not name or name == REQUEST_CONFIRMATION_FC:
        continue
      emitted = True
      tool_id = getattr(fc, "id", None) or f"tool_{uuid.uuid4().hex[:12]}"
      args = getattr(fc, "args", None) or {}
      if not isinstance(args, dict):
        try:
          args = dict(args)
        except Exception:
          args = {"value": str(args)}
      args_summary = format_tool_call_extras(fc)
      _sse_emit(
        {
          "type": "tool_use",
          "tool": {
            "id": tool_id,
            "name": name,
            "input": args,
            "args_summary": args_summary,
          },
        }
      )
  except Exception:
    pass
  return emitted


def _is_permission_block_response(response: Any) -> bool:
  """True when a tool was blocked pending user confirmation (not a real failure)."""
  try:
    d = response if isinstance(response, dict) else {}
    inner = d.get("result", d)
    if not isinstance(inner, dict):
      inner = d
    if inner.get("error_kind") == "permission_block":
      return True
    err = inner.get("error") or d.get("error")
    return bool(err and "requires confirmation" in str(err))
  except Exception:
    return False


def _emit_tool_results(
  event: Any,
  *,
  skip_response: Callable[..., bool] | None = None,
) -> bool:
  """Emit tool_result frames. Returns True if any result was emitted."""
  emitted = False
  try:
    frs: list = []
    try:
      frs = event.get_function_responses() or []
    except Exception:
      frs = []
    if not frs and getattr(event, "content", None) and getattr(event.content, "parts", None):
      for part in event.content.parts:
        fr = getattr(part, "function_response", None)
        if fr is not None:
          frs.append(fr)
    for fr in frs:
      name = getattr(fr, "name", "") or ""
      if not name or name == REQUEST_CONFIRMATION_FC:
        continue
      emitted = True
      tool_id = getattr(fr, "id", None) or f"tool_{uuid.uuid4().hex[:12]}"
      resp = getattr(fr, "response", {}) or {}
      if skip_response and skip_response(resp, name=name):
        continue
      result_text, is_error, summary = _tool_result_text(resp)
      _sse_emit(
        {
          "type": "tool_result",
          "tool": {
            "id": tool_id,
            "name": name,
            "result": result_text,
            "summary": summary,
            "is_error": is_error,
          },
        }
      )
  except Exception:
    pass
  return emitted


def _confirmation_fcs_in_event(event: Any) -> list[Any]:
  """Return adk_request_confirmation function calls from a single ADK event."""
  out: list[Any] = []
  try:
    for fc in event.get_function_calls() or []:
      if getattr(fc, "name", None) == REQUEST_CONFIRMATION_FC:
        out.append(fc)
  except Exception:
    pass
  if out:
    return out
  try:
    content = getattr(event, "content", None)
    for part in getattr(content, "parts", None) or []:
      fc = getattr(part, "function_call", None)
      if fc is not None and getattr(fc, "name", None) == REQUEST_CONFIRMATION_FC:
        out.append(fc)
  except Exception:
    pass
  return out


def _get_confirmation_requests(events: list[Any]) -> list[Any]:
  """Return confirmation FCs from the last event in the batch that has any.

  The ADK runner expects function responses only for the function calls in
  the most recent event.  Responding to FCs from earlier events in the same
  batch raises:
    ValueError: Last response event should only contain the responses for
      the function calls in the same function call event.
  """
  for ev in reversed(events):
    fcs = _confirmation_fcs_in_event(ev)
    if fcs:
      return fcs
  return []


def _permission_block_hint(response: Any) -> str:
  try:
    d = response if isinstance(response, dict) else {}
    inner = d.get("result", d)
    if not isinstance(inner, dict):
      inner = d
    err = inner.get("error") or d.get("error")
    return str(err) if err else ""
  except Exception:
    return ""


async def _resolve_web_tool_permission(
  cfg: GemCodeConfig,
  *,
  session_id: str,
  tool_name: str,
  hint: str,
  fc_id: str | None = None,
  approval_id: str | None = None,
) -> bool:
  auto = getattr(cfg, "_web_auto_approve", None)
  if isinstance(auto, dict) and _web_tool_auto_approved(tool_name, auto):
    return True
  if getattr(cfg, "yes_to_all", False) or getattr(cfg, "super_mode", False):
    return True

  from gemcode.web.hitl_bridge import new_approval_id, register_pending_approval, wait_for_web_approval

  aid = (approval_id or "").strip() or new_approval_id(session_id, fc_id or tool_name)
  already_emitted = bool((approval_id or "").strip())
  register_pending_approval(aid)
  if not already_emitted:
    _sse_emit(
      {
        "type": "permission_request",
        "approval_id": aid,
        "tool": tool_name,
        "hint": hint,
        "message": f"Allow tool '{tool_name}'?",
      }
    )
  _sse_emit(
    {
      "type": "status",
      "phase": "working",
      "message": "Awaiting your approval — tap Yes or No below",
      "approval_id": aid,
      "permission_tool": tool_name,
      "hint": hint,
    }
  )
  confirmed = await wait_for_web_approval(
    aid,
    heartbeat=lambda: _sse_keepalive(
      message="Awaiting your approval — tap Yes or No below",
    ),
  )
  _sse_emit(
    {
      "type": "turn_notice",
      "level": "info" if confirmed else "warn",
      "code": "permission_granted" if confirmed else "permission_denied",
      "message": (
        f"Approved tool: {tool_name}"
        if confirmed
        else f"Denied tool: {tool_name}"
      ),
    }
  )
  return confirmed


def _tool_needs_web_approval(cfg: GemCodeConfig, tool_name: str) -> bool:
  if getattr(cfg, "yes_to_all", False) or getattr(cfg, "super_mode", False):
    return False
  auto = getattr(cfg, "_web_auto_approve", None)
  if isinstance(auto, dict) and _web_tool_auto_approved(tool_name, auto):
    return False
  if tool_name in SHELL_TOOLS:
    return True
  if tool_name in MUTATING_TOOLS:
    return True
  return False


def _inject_web_code_context(cfg: GemCodeConfig, prompt: str, req: dict[str, Any]) -> str:
  """Attach workspace + editor context for Code/Agents mode in the web UI."""
  mode = getattr(cfg, "_web_workspace_mode", None)
  if mode not in ("code", "agents"):
    return prompt

  root = str(cfg.project_root.resolve())
  lines = [
    f"**Workspace mode: {mode}** — you are connected to the user's local project with full filesystem tools "
    "(`read_file`, `list_directory`, `glob_files`, `grep_content`, `repo_map`, `write_file`, `bash`, etc.). "
    "Use them proactively; never tell the user you cannot access their files.",
    f"Workspace root: `{root}`",
    "Tool paths are relative to this workspace root.",
  ]

  active = req.get("active_file")
  has_active = isinstance(active, str) and active.strip()
  if has_active:
    rel = active.strip()
    lines.append(f'Active file in the web editor (user\'s "current file"): `{rel}`')
    try:
      object.__setattr__(cfg, "_web_active_file", rel)
    except Exception:
      pass

  open_files = req.get("open_files")
  open_paths: list[str] = []
  if isinstance(open_files, list):
    open_paths = [str(f).strip() for f in open_files if str(f).strip()][:12]
    if open_paths:
      lines.append("Open editor tabs: " + ", ".join(f"`{p}`" for p in open_paths))

  referenced = req.get("referenced_files")
  ref_paths: list[str] = []
  if isinstance(referenced, list):
    ref_paths = [str(f).strip() for f in referenced if str(f).strip()][:12]
    if ref_paths:
      lines.append(
        "Files @-referenced in the composer: " + ", ".join(f"`{p}`" for p in ref_paths)
      )

  restricted = getattr(cfg, "_web_restricted_dirs", None)
  if isinstance(restricted, list) and restricted:
    lines.append(
      "**Restricted directories (web UI)** — only read/write paths under: "
      + ", ".join(f"`{d}`" for d in restricted)
      + ". Refuse or ask before touching anything outside these roots."
    )

  if not has_active:
    if ref_paths:
      lines.append(
        f'No editor tab is focused; treat `{ref_paths[0]}` as the user\'s "current file" unless they name another path.'
      )
    elif open_paths:
      lines.append(
        f'No editor tab is focused; the frontmost open tab is `{open_paths[0]}`.'
      )
    else:
      lines.append(
        "No file is open or @-referenced in the web UI. "
        'For vague "this file" / "the current one" requests, ask them to open a file in the Files panel '
        "or type @path — unless they mean the whole project (see below)."
      )

  # Permission mode for THIS turn (must match actual HITL / auto-approve behavior).
  lines.append(
    'When the user says "this file", "the current one", or similar, use the active / @-referenced file above.'
  )
  lines.append(
    'When they ask to analyze the whole codebase, project, repo, or "all files", start with `repo_map` and '
    '`list_directory` on `.` — do not ask which file first.'
  )
  auto_on = bool(
    getattr(cfg, "yes_to_all", False)
    or getattr(cfg, "super_mode", False)
    or not getattr(cfg, "_web_interactive_hitl", True)
  )
  if auto_on:
    lines.append(
      "**Web UI permissions — AUTO-APPROVE is ON for this turn.** "
      "Shell and mutating tools run immediately with **no** Yes/No card. "
      "Never mention Approve, Deny, approval prompts, dialogs, or waiting for the user — "
      "just run tools and report what you did."
    )
  else:
    lines.append(
      "**Web UI permissions — interactive.** Shell and mutating tools pause for an "
      "**inline Yes/No card in the chat**. "
      "Do **not** invent Approve/Deny dialog text or tell the user to open a popup — "
      "wait silently; after they tap Yes or No the turn continues."
    )
  lines.append(
    "**Web preview:** For static HTML/CSS/JS, prefer `http://localhost:8000/...` links. "
    "GemCode will auto-serve the workspace on common static ports (8000/8080/…) when "
    "nothing is listening. For Vite/Next (3000/5173), start the real dev server with bash first."
  )

  if mode == "agents":
    lines.extend(
      [
        "**Agents mode** — use GemCode's org mesh for multi-agent work:",
        "`org_list` / `org_tree` — see specialist agents; `org_delegate(member, task)` — background delegation;",
        "`org_spawn` — hire + delegate; `run_subtask` / `spawn_subtasks` — parallel isolated work;",
        "`mesh_status` / `mesh_halt` — queue control. Prefer delegating large independent tasks to agents.",
      ]
    )

  block = "## Web IDE context\n" + "\n".join(f"- {line}" for line in lines)
  return f"{block}\n\n---\n\n{prompt}"


def _ensure_web_runtime(cfg: GemCodeConfig) -> None:
  load_cli_environment()
  if not os.environ.get("GOOGLE_API_KEY") and not os.environ.get("GEMCODE_WEB_MOCK_RESPONSE"):
    raise ValueError(
      "GOOGLE_API_KEY is not set. Add it to .env or run: gemcode login"
    )
  from gemcode.cli import _initialize_gemcode_project

  _initialize_gemcode_project(cfg)


async def _emit_assistant_text_chunks(text: str, assistant_block_index: int) -> None:
  """Stream pre-baked assistant text (slash command replies, etc.)."""
  full = (text or "").strip()
  if not full:
    return
  chunk_size = int(os.environ.get("GEMCODE_WEB_MOCK_CHUNK", "24"))
  for i in range(0, len(full), max(1, chunk_size)):
    delta = full[i : i + chunk_size]
    _sse_emit(
      {
        "type": "content_block_delta",
        "index": assistant_block_index,
        "delta": {"type": "text_delta", "text": delta},
      }
    )
    _sse_emit({"type": "text", "content": delta})
    await asyncio.sleep(0.005)


async def _stream_gemcode_turn(
    cfg: GemCodeConfig,
    *,
    user_id: str,
    session_id: str,
    prompt: str,
    assistant_block_index: int,
    runner: Any,
    attachment_paths: list[Path] | None = None,
) -> str:
  """Stream one GemCode turn through the real runner (tools + text)."""
  from google.adk.agents.run_config import RunConfig
  from google.genai import types

  from gemcode.invoke import prepare_turn_prompt

  emitted_text = ""
  emitted_thought = ""
  collected: list[Any] = []
  turn_t0 = time.monotonic()
  had_tool_calls = False

  stop_heartbeat = asyncio.Event()

  async def _turn_heartbeat() -> None:
    while not stop_heartbeat.is_set():
      await asyncio.sleep(20.0)
      if stop_heartbeat.is_set():
        break
      _sse_keepalive(
        message="Still working…",
        elapsed_s=time.monotonic() - turn_t0,
      )

  heartbeat_task = asyncio.create_task(_turn_heartbeat())

  try:
    enriched = prepare_turn_prompt(cfg, prompt, session_id=session_id)
    run_config = (
      RunConfig(max_llm_calls=cfg.max_llm_calls) if cfg.max_llm_calls is not None else None
    )
    _emit_status("thinking", elapsed_s=0.0)

    web_hitl = bool(
      getattr(cfg, "_gemcode_web_sse", False)
      or getattr(cfg, "_web_interactive_hitl", False)
    ) and not (getattr(cfg, "yes_to_all", False) or getattr(cfg, "super_mode", False))
    skipped_permission_blocks: list[tuple[str, str]] = []
    # approval_id, tool_name, hint, tool_fc_id — emitted at tool_call time so UI shows immediately
    preflight_approvals: list[tuple[str, str, str, str]] = []

    def _emit_tool_calls_and_preflight(event: Any) -> bool:
      emitted = _emit_tool_calls(event)
      if not web_hitl:
        return emitted
      try:
        from gemcode.web.hitl_bridge import new_approval_id, register_pending_approval

        for fc in event.get_function_calls() or []:
          name = getattr(fc, "name", "") or ""
          if not name or name == REQUEST_CONFIRMATION_FC:
            continue
          if not _tool_needs_web_approval(cfg, name):
            continue
          tool_id = str(getattr(fc, "id", None) or f"tool_{uuid.uuid4().hex[:12]}")
          if any(tid == tool_id for *_, tid in preflight_approvals):
            continue
          hint = format_tool_call_extras(fc) or ""
          approval_id = new_approval_id(session_id, tool_id)
          register_pending_approval(approval_id)
          preflight_approvals.append((approval_id, name, hint, tool_id))
          _sse_emit(
            {
              "type": "permission_request",
              "approval_id": approval_id,
              "tool": name,
              "hint": hint,
              "message": f"Allow tool '{name}'?",
            }
          )
          _sse_emit(
            {
              "type": "status",
              "phase": "working",
              "message": "Awaiting your approval — tap Yes or No below",
              "approval_id": approval_id,
              "permission_tool": name,
              "hint": hint,
            }
          )
      except Exception:
        pass
      return emitted

    def _preflight_id_for_tool(tool_name: str, fc_id: str | None) -> str | None:
      if fc_id:
        for aid, name, _, tid in preflight_approvals:
          if tid == fc_id:
            return aid
      for aid, name, _, _ in preflight_approvals:
        if name == tool_name:
          return aid
      if len(preflight_approvals) == 1:
        return preflight_approvals[0][0]
      return None

    def _skip_permission_block(resp: Any, name: str = "") -> bool:
      if not (web_hitl and _is_permission_block_response(resp)):
        return False
      hint = _permission_block_hint(resp)
      skipped_permission_blocks.append((name or "tool", hint))
      return True

    if attachment_paths:
      from gemcode.multimodal_input import build_user_content

      object.__setattr__(cfg, "_attachments_allowed", True)
      current_message, attach_warn = build_user_content(
        enriched, attachment_paths, project_root=cfg.project_root
      )
      for w in attach_warn:
        _sse_emit({"type": "turn_notice", "level": "warn", "message": w})
    else:
      current_message = types.Content(role="user", parts=[types.Part(text=enriched)])

    def _extract_hint_and_tool(fc) -> tuple[str, str]:
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

    while True:
      batch: list[Any] = []
      async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=current_message,
        **({"run_config": run_config} if run_config is not None else {}),
      ):
        batch.append(event)
        elapsed = time.monotonic() - turn_t0

        if _emit_tool_calls_and_preflight(event):
          had_tool_calls = True
          _emit_status("running", elapsed_s=elapsed)

        if _emit_tool_results(event, skip_response=_skip_permission_block):
          _emit_status("querying", elapsed_s=elapsed)

        final_text, thought_text = extract_parts_from_event(event)

        if thought_text:
          if thought_text.startswith(emitted_thought):
            thought_delta = thought_text[len(emitted_thought) :]
          else:
            common = 0
            max_common = min(len(thought_text), len(emitted_thought))
            while common < max_common and thought_text[common] == emitted_thought[common]:
              common += 1
            thought_delta = thought_text[common:]
          if thought_delta:
            emitted_thought += thought_delta
            _emit_status("thinking", message="Thinking…", elapsed_s=elapsed)
            await _emit_thinking_delta(thought_delta)

        if final_text:
          if final_text.startswith(emitted_text):
            delta = final_text[len(emitted_text) :]
          else:
            common = 0
            max_common = min(len(final_text), len(emitted_text))
            while common < max_common and final_text[common] == emitted_text[common]:
              common += 1
            delta = final_text[common:]
          if delta:
            emitted_text += delta
            if not had_tool_calls:
              _emit_status("thinking", message="Thinking…", elapsed_s=elapsed)
            await _emit_text_delta(assistant_block_index, delta)

      collected.extend(batch)
      confirmation_fcs = _get_confirmation_requests(batch)

      if confirmation_fcs:
        parts: list[types.Part] = []
        approved_any = False
        for fc in confirmation_fcs:
          tool_name, hint = _extract_hint_and_tool(fc)
          orig = (getattr(fc, "args", None) or {}).get("originalFunctionCall") or {}
          orig_id = str(orig.get("id") or "") or None
          pre_id = _preflight_id_for_tool(tool_name, orig_id)
          ok = await _resolve_web_tool_permission(
            cfg,
            session_id=session_id,
            tool_name=tool_name,
            hint=hint,
            fc_id=str(getattr(fc, "id", None) or "") or None,
            approval_id=pre_id,
          )
          if ok:
            approved_any = True
          parts.append(
            types.Part(
              function_response=types.FunctionResponse(
                name=REQUEST_CONFIRMATION_FC,
                id=getattr(fc, "id", None),
                response={"confirmed": bool(ok)},
              )
            )
          )
        preflight_approvals.clear()
        skipped_permission_blocks.clear()
        current_message = types.Content(role="user", parts=parts)
        if not approved_any:
          break
        continue

      if skipped_permission_blocks and web_hitl:
        approved_any = False
        for tool_name, hint in skipped_permission_blocks:
          pre_id = _preflight_id_for_tool(tool_name, None)
          if await _resolve_web_tool_permission(
            cfg,
            session_id=session_id,
            tool_name=tool_name,
            hint=hint,
            approval_id=pre_id,
          ):
            approved_any = True
        skipped_permission_blocks.clear()
        preflight_approvals.clear()
        if approved_any:
          current_message = types.Content(
            role="user",
            parts=[
              types.Part(
                text=(
                  "[Web UI] The user approved the pending tool action in the browser. "
                  "Retry the last shell or mutating tool call now."
                )
              )
            ],
          )
          continue
        break

      if preflight_approvals and web_hitl:
        approved_any = False
        for approval_id, tool_name, hint, _ in preflight_approvals:
          if await _resolve_web_tool_permission(
            cfg,
            session_id=session_id,
            tool_name=tool_name,
            hint=hint,
            approval_id=approval_id,
          ):
            approved_any = True
        preflight_approvals.clear()
        if approved_any:
          current_message = types.Content(
            role="user",
            parts=[
              types.Part(
                text=(
                  "[Web UI] The user approved the pending tool action in the browser. "
                  "Retry the last shell or mutating tool call now."
                )
              )
            ],
          )
          continue
        break

      break

    _emit_status("idle")

    if had_tool_calls and not emitted_text.strip():
      _sse_emit(
        {
          "type": "turn_notice",
          "level": "info",
          "code": "tools_only",
          "message": "Tools ran but the model returned no text reply.",
        }
      )

    stats = getattr(cfg, "_last_turn_stats", None)
    if isinstance(stats, dict) and stats:
      try:
        from gemcode.pricing import format_cost, format_tokens

        in_tok = stats.get("in", 0) or 0
        out_tok = stats.get("out", 0) or 0
        think_tok = stats.get("think", 0) or 0
        turn_cost = stats.get("turn_cost")
        parts_t = [f"↑{format_tokens(in_tok)}", f"↓{format_tokens(out_tok)}"]
        if think_tok:
          parts_t.append(f"✦{format_tokens(think_tok)}")
        if turn_cost is not None:
          parts_t.append(format_cost(turn_cost))
        _sse_emit(
          {
            "type": "usage",
            "usage": {
              "input_tokens": in_tok,
              "output_tokens": out_tok,
              "thinking_tokens": think_tok,
              "turn_cost_usd": turn_cost,
            },
            "message": "  ·  ".join(parts_t),
          }
        )
      except Exception:
        pass

    try:
      from gemcode.agent_intelligence import post_turn_learn

      post_turn_learn(cfg, collected)
    except Exception:
      pass

    return emitted_text
  finally:
    stop_heartbeat.set()
    heartbeat_task.cancel()
    try:
      await heartbeat_task
    except asyncio.CancelledError:
      pass


def _materialize_web_attachments(req: dict[str, Any]) -> tuple[list[Path], list[str]]:
  raw = req.get("attachments")
  if not isinstance(raw, list) or not raw:
    return [], []
  from gemcode.ide_stdio import prepare_inline_attachment_paths

  paths, errors = prepare_inline_attachment_paths(raw)
  return paths, errors


def _web_tool_auto_approved(tool_name: str, auto: dict[str, bool]) -> bool:
  if tool_name in SHELL_TOOLS:
    return bool(auto.get("bash"))
  if tool_name in MUTATING_TOOLS:
    return bool(auto.get("file_write"))
  if tool_name in READ_ONLY_TOOLS:
    return bool(auto.get("file_read", True))
  if tool_name in _WEB_SEARCH_TOOLS or "search" in tool_name.lower():
    return bool(auto.get("web_search"))
  return bool(auto.get("file_write"))


def _apply_web_permissions(cfg: GemCodeConfig, req: dict[str, Any]) -> None:
  perms = req.get("permissions")
  if not isinstance(perms, dict):
    return
  auto = perms.get("auto_approve")
  if not isinstance(auto, dict):
    auto = {}
  normalized = {
    "bash": bool(auto.get("bash")),
    "file_read": bool(auto.get("file_read")),
    "file_write": bool(auto.get("file_write")),
    "web_search": bool(auto.get("web_search")),
  }
  object.__setattr__(cfg, "_web_auto_approve", normalized)

  # UI is source of truth for web chat — do not leave env GEMCODE_SUPER_MODE stuck on.
  super_mode = bool(perms.get("super_mode"))
  if super_mode:
    cfg.super_mode = True
    from gemcode.config import apply_super_mode

    apply_super_mode(cfg)
    object.__setattr__(cfg, "_web_interactive_hitl", False)
    return

  cfg.super_mode = False
  if all(normalized.values()):
    cfg.yes_to_all = True
    cfg.interactive_permission_ask = False
    object.__setattr__(cfg, "_web_interactive_hitl", False)
  else:
    cfg.yes_to_all = False
    cfg.interactive_permission_ask = True
    object.__setattr__(cfg, "_web_interactive_hitl", True)

  restricted = perms.get("restricted_dirs")
  if isinstance(restricted, list):
    dirs = [str(d).strip() for d in restricted if str(d).strip()]
    if dirs:
      object.__setattr__(cfg, "_web_restricted_dirs", dirs)


def _configure_web_permissions(cfg: GemCodeConfig, req: dict[str, Any]) -> None:
  """Web chat HITL follows the UI request, not process-level GEMCODE_SUPER_MODE.

  Hosted tenant images historically set ``GEMCODE_SUPER_MODE=1`` so mesh/jobs are
  unattended. That must not silently skip Yes/No cards when the user has
  Auto-approve off in the web UI. Mesh workers still use
  ``GEMCODE_MESH_WORKER_UNATTENDED`` independently.
  """
  object.__setattr__(cfg, "_gemcode_web_sse", True)

  perms = req.get("permissions")
  has_perms = isinstance(perms, dict)

  # Clear env-applied super mode before applying the UI payload.
  if not (has_perms and bool(perms.get("super_mode"))):
    cfg.super_mode = False
    cfg.yes_to_all = False
    cfg.interactive_permission_ask = True
    object.__setattr__(cfg, "_web_interactive_hitl", True)

  if has_perms:
    _apply_web_permissions(cfg, req)
  else:
    cfg.super_mode = False
    cfg.yes_to_all = False
    cfg.interactive_permission_ask = True
    object.__setattr__(cfg, "_web_interactive_hitl", True)

  _ensure_web_hitl(cfg)
  if os.environ.get("GEMCODE_WEB_YES_TO_ALL", "").lower() in ("1", "true", "yes", "on"):
    cfg.yes_to_all = True
    cfg.interactive_permission_ask = False
    object.__setattr__(cfg, "_web_interactive_hitl", False)


def _ensure_web_hitl(cfg: GemCodeConfig) -> None:
  """Web chat uses the UI approval bridge when tools are not fully auto-approved."""
  if getattr(cfg, "yes_to_all", False) or getattr(cfg, "super_mode", False):
    object.__setattr__(cfg, "_web_interactive_hitl", False)
    return
  cfg.interactive_permission_ask = True
  object.__setattr__(cfg, "_web_interactive_hitl", True)


def _apply_web_request_options(cfg: GemCodeConfig, req: dict[str, Any], workspace_mode: str) -> str:
  """Apply per-request toggles from the web UI onto cfg. Returns effective workspace_mode."""
  model_mode = req.get("model_mode")
  if isinstance(model_mode, str) and model_mode.strip():
    cfg.model_mode = model_mode.strip().lower()

  thinking_mode = req.get("thinking_mode")
  if isinstance(thinking_mode, str):
    tm = thinking_mode.strip().lower()
    if tm == "off":
      cfg.disable_thinking = True
      cfg.show_full_thinking = False
    elif tm == "verbose":
      cfg.disable_thinking = False
      cfg.show_full_thinking = True
      cfg.include_thought_summaries = True
    elif tm == "brief":
      cfg.disable_thinking = False
      cfg.show_full_thinking = False
    elif tm == "auto":
      cfg.disable_thinking = False
      cfg.show_full_thinking = False

  caps = req.get("capabilities")
  if isinstance(caps, dict):
    if "plan_mode" in caps:
      cfg.plan_mode = bool(caps.get("plan_mode"))
    if "enable_memory" in caps:
      cfg.enable_memory = bool(caps.get("enable_memory"))
    if "enable_web_search" in caps:
      cfg.enable_web_search = bool(caps.get("enable_web_search"))
    if "enable_deep_research" in caps:
      cfg.enable_deep_research = bool(caps.get("enable_deep_research"))
    if "enable_computer_use" in caps:
      cfg.enable_computer_use = bool(caps.get("enable_computer_use"))
    if "enable_embeddings" in caps:
      cfg.enable_embeddings = bool(caps.get("enable_embeddings"))
    if "enable_maps_grounding" in caps:
      cfg.enable_maps_grounding = bool(caps.get("enable_maps_grounding"))
    if "enable_code_executor" in caps:
      cfg.enable_code_executor = bool(caps.get("enable_code_executor"))
    cap_mode = caps.get("capability_mode")
    if isinstance(cap_mode, str) and cap_mode.strip():
      cfg.capability_mode = cap_mode.strip().lower()
    if bool(caps.get("proposal_mode")):
      object.__setattr__(cfg, "ide_proposal_mode", True)
      object.__setattr__(cfg, "ide_allow_write", True)
      object.__setattr__(cfg, "ide_allow_shell", True)
      object.__setattr__(cfg, "_ide_emitter", _WebIdeEmitter())
    style = caps.get("output_style")
    if isinstance(style, str) and style.strip():
      cfg.output_style = style.strip().lower()

  system_prompt = req.get("system_prompt")
  if isinstance(system_prompt, str) and system_prompt.strip():
    object.__setattr__(cfg, "_web_system_prompt", system_prompt.strip())

  temp = req.get("temperature")
  if temp is not None:
    try:
      object.__setattr__(cfg, "_web_temperature", float(temp))
    except (TypeError, ValueError):
      pass

  max_tok = req.get("max_output_tokens") or req.get("max_tokens")
  if max_tok is not None:
    try:
      object.__setattr__(cfg, "_web_max_output_tokens", int(max_tok))
    except (TypeError, ValueError):
      pass

  thinking_budget = req.get("thinking_budget")
  if thinking_budget is not None:
    try:
      cfg.thinking_budget = int(thinking_budget)
    except (TypeError, ValueError):
      pass

  thinking_level = req.get("thinking_level")
  if isinstance(thinking_level, str) and thinking_level.strip():
    cfg.thinking_level = thinking_level.strip().lower()

  return workspace_mode


async def run_adapter(req: dict[str, Any]) -> None:
  messages = req.get("messages")
  requested_model = req.get("model")
  model = requested_model or os.environ.get("GEMCODE_MODEL") or "gemini-3.1-pro-preview"

  if not isinstance(messages, list):
    raise ValueError("messages must be a list")

  session_id = str(req.get("session_id") or uuid.uuid4())
  if req.get("session_id"):
    prompt = _latest_user_message(messages) or _build_prompt(messages)
  else:
    prompt = _build_prompt(messages)
  if not prompt.strip():
    raise ValueError("No user message in request")

  from gemcode.web.project_root import HostedTenantPathError, resolve_sse_project_root

  req_root = req.get("project_root")
  raw_root = req_root.strip() if isinstance(req_root, str) and req_root.strip() else None
  try:
    root_path = resolve_sse_project_root(raw_root)
  except HostedTenantPathError as exc:
    raise ValueError(str(exc)) from exc
  if not root_path.is_dir():
    raise ValueError(f"project_root is not a directory: {root_path}")
  project_root = str(root_path)
  from gemcode.org import resolve_fleet_root

  fleet_root = resolve_fleet_root(root_path.resolve())
  cfg = GemCodeConfig(project_root=fleet_root)

  from gemcode.trust import ensure_hosted_workspace_trust

  ensure_hosted_workspace_trust(fleet_root)

  if req.get("session_id"):
    try:
      from gemcode.session_store import touch_session

      touch_session(fleet_root, session_id)
    except Exception:
      pass

  workspace_mode = str(req.get("workspace_mode") or "code").strip().lower()
  if workspace_mode == "cowork":
    workspace_mode = "agents"
  if workspace_mode not in ("chat", "agents", "code"):
    workspace_mode = "code"
  object.__setattr__(cfg, "_web_workspace_mode", workspace_mode)
  if workspace_mode == "chat":
    caps = req.get("capabilities")
    chat_research = isinstance(caps, dict) and bool(caps.get("enable_deep_research"))
    chat_computer = isinstance(caps, dict) and bool(caps.get("enable_computer_use"))
    chat_embeddings = isinstance(caps, dict) and bool(caps.get("enable_embeddings"))
    chat_maps = isinstance(caps, dict) and bool(caps.get("enable_maps_grounding"))
    if not chat_research:
      try:
        cfg.enable_deep_research = False
        object.__setattr__(cfg, "enable_deep_research", False)
      except Exception:
        pass
    if not chat_computer:
      try:
        cfg.enable_computer_use = False
      except Exception:
        pass
    if not chat_embeddings:
      try:
        cfg.enable_embeddings = False
      except Exception:
        pass
    if not chat_maps:
      try:
        cfg.enable_maps_grounding = False
      except Exception:
        pass

  workspace_mode = _apply_web_request_options(cfg, req, workspace_mode)

  cfg.permission_mode = os.environ.get("GEMCODE_PERMISSION_MODE", cfg.permission_mode)
  _configure_web_permissions(cfg, req)

  attachment_paths, attach_errors = _materialize_web_attachments(req)
  for err in attach_errors:
    _sse_emit({"type": "turn_notice", "level": "warn", "message": err})

  MODEL_MAP: dict[str, str] = {
    # Legacy UI aliases → current Gemini models
    "gemcode-pro": "gemini-3.1-pro-preview",
    "gemcode-balanced": "gemini-3.5-flash",
    "gemcode-fast": "gemini-3.1-flash-lite",
  }

  resolved_model: str | None = None
  if isinstance(requested_model, str) and requested_model.strip():
    rm = requested_model.strip()
    if rm in MODEL_MAP:
      resolved_model = MODEL_MAP[rm]
    elif rm.startswith("gemini") or rm.startswith("models/"):
      resolved_model = rm

  if resolved_model:
    cfg.model = resolved_model
    cfg.model_overridden = True
    model = resolved_model

  message_id = f"msg_{uuid.uuid4().hex[:12]}"
  assistant_block_index = 0

  _sse_emit(
    {
      "type": "message_start",
      "message": {
        "id": message_id,
        "role": "assistant",
        "model": model,
        "usage": {"input_tokens": 0, "output_tokens": 0},
      },
    }
  )
  _sse_emit(
    {
      "type": "content_block_start",
      "index": assistant_block_index,
      "content_block": {"type": "text", "text": ""},
    }
  )

  allow_mock = os.environ.get("GEMCODE_WEB_ALLOW_MOCK", "").lower() in (
    "1",
    "true",
    "yes",
    "on",
  )
  mock_response = os.environ.get("GEMCODE_WEB_MOCK_RESPONSE") if allow_mock else None
  if isinstance(mock_response, str) and mock_response.strip():
    _sse_emit({"type": "meta", "agent": "mock", "mock": True})
    full = mock_response
    chunk_size = int(os.environ.get("GEMCODE_WEB_MOCK_CHUNK", "6"))
    for i in range(0, len(full), max(1, chunk_size)):
      delta = full[i : i + chunk_size]
      _sse_emit(
        {
          "type": "content_block_delta",
          "index": assistant_block_index,
          "delta": {"type": "text_delta", "text": delta},
        }
      )
      _sse_emit({"type": "text", "content": delta})
      await asyncio.sleep(0.01)
  else:
    _ensure_web_runtime(cfg)
    cfg.session_skill_expand_session_id = session_id
    runner = create_runner(cfg, extra_tools=None)
    try:
      from gemcode.web.chat_skills import resolve_web_chat_input

      resolved = await resolve_web_chat_input(
        cfg,
        prompt,
        session_id=session_id,
        runner=runner,
      )

      if resolved.force_rebuild_runner:
        try:
          close = runner.close()
          if asyncio.iscoroutine(close):
            await close
        except Exception:
          pass
        runner = create_runner(cfg, extra_tools=None)

      if resolved.use_code_tools and workspace_mode == "chat":
        object.__setattr__(cfg, "_web_workspace_mode", "code")
        workspace_mode = "code"
        try:
          close = runner.close()
          if asyncio.iscoroutine(close):
            await close
        except Exception:
          pass
        runner = create_runner(cfg, extra_tools=None)

      _sse_emit(
        {
          "type": "meta",
          "agent": "gemcode",
          "mock": False,
          "model": model,
          "project_root": str(cfg.project_root),
          "workspace_mode": workspace_mode,
          "hitl_interactive": bool(getattr(cfg, "_web_interactive_hitl", False)),
          "auto_approve": bool(
            getattr(cfg, "yes_to_all", False) or getattr(cfg, "super_mode", False)
          ),
          **({"skill": resolved.skill_name} if resolved.skill_name else {}),
        }
      )

      if resolved.direct_response is not None:
        await _emit_assistant_text_chunks(resolved.direct_response, assistant_block_index)
      else:
        await _stream_gemcode_turn(
          cfg,
          user_id=str(req.get("user_id") or "web"),
          session_id=session_id,
          prompt=_inject_web_code_context(cfg, resolved.prompt, req),
          assistant_block_index=assistant_block_index,
          runner=runner,
          attachment_paths=attachment_paths or None,
        )
    finally:
      try:
        close = runner.close()
        if asyncio.iscoroutine(close):
          await close
      except Exception:
        pass

  _sse_emit({"type": "content_block_stop", "index": assistant_block_index})
  _sse_emit({"type": "message_stop"})
  _sse_emit({"type": "done"})


def main() -> None:
  from asyncio import run

  req = json.loads(sys.stdin.read() or "{}")
  try:
    run(run_adapter(req))
  except Exception as exc:
    _sse_emit({"type": "error", "error": str(exc)})
    _sse_emit({"type": "done"})
    sys.exit(1)


if __name__ == "__main__":
  main()
