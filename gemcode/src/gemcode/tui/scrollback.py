from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass

from google.adk.agents.run_config import RunConfig
from google.genai import types
from rich.console import Console

from gemcode.capability_routing import apply_capability_routing
from gemcode.config import load_cli_environment
from gemcode.model_routing import pick_effective_model
from gemcode.repl_slash import process_repl_slash
from gemcode.tui.input_handler import GemCodeInputHandler
from gemcode.tui.welcome_rich import print_shortcuts_hint, print_welcome_dashboard

_ADK_REQUEST_CONFIRMATION = "adk_request_confirmation"


def format_tool_call_extras(fc) -> str:
  """
  One-line summary of tool arguments for Claude-style ``[tool] name …`` lines.

  Parses ``FunctionCall.args`` / nested ``originalFunctionCall.args`` when present.
  """
  try:
    raw = getattr(fc, "args", None)
    if raw is None:
      return ""
    if isinstance(raw, str):
      try:
        raw = json.loads(raw)
      except Exception:
        return ""
    if not isinstance(raw, dict):
      return ""
    inner: dict = {}
    orig = raw.get("originalFunctionCall")
    if isinstance(orig, dict):
      a = orig.get("args")
      if isinstance(a, dict):
        inner = a
      elif isinstance(a, str):
        try:
          inner = json.loads(a) if a.strip() else {}
        except Exception:
          inner = {}
    if not inner:
      inner = {
          k: v
          for k, v in raw.items()
          if k not in ("originalFunctionCall", "toolConfirmation")
      }
    if not isinstance(inner, dict) or not inner:
      return ""
    for key in (
        "path",
        "glob_pattern",
        "pattern",
        "command",
        "query",
        "url",
        "file_path",
        "target_file",
    ):
      if key in inner and inner[key] not in (None, ""):
        v = str(inner[key])
        if len(v) > 80:
          v = v[:77] + "..."
        return f"{key}={v}"
    parts: list[str] = []
    for k, v in list(inner.items())[:4]:
      if k in ("originalFunctionCall",):
        continue
      sv = str(v)
      if len(sv) > 40:
        sv = sv[:37] + "..."
      parts.append(f"{k}={sv}")
    return " ".join(parts) if parts else ""
  except Exception:
    return ""


def _events_had_non_confirmation_tools(events: list) -> bool:
  for ev in events:
    try:
      for fc in ev.get_function_calls() or []:
        if getattr(fc, "name", "") != _ADK_REQUEST_CONFIRMATION:
          return True
    except Exception:
      continue
  return False


@dataclass(frozen=True)
class _Ansi:
  enabled: bool

  def esc(self, code: str) -> str:
    if not self.enabled:
      return ""
    return f"\x1b[{code}m"

  @property
  def reset(self) -> str:  # noqa: D401
    return self.esc("0")

  @property
  def dim(self) -> str:
    return self.esc("2")

  @property
  def bold(self) -> str:
    return self.esc("1")

  @property
  def blue(self) -> str:
    # ANSI 256-color bright-ish blue
    return self.esc("38;5;75")

  @property
  def blue2(self) -> str:
    return self.esc("38;5;33")

  @property
  def blue_ok(self) -> str:
    return self.esc("38;5;81")

  @property
  def blue_warn(self) -> str:
    return self.esc("38;5;39")

  @property
  def blue_tool(self) -> str:
    return self.esc("38;5;69")


def _term_width(default: int = 100) -> int:
  try:
    import shutil

    return max(60, shutil.get_terminal_size((default, 24)).columns)
  except Exception:
    return default


def _hr(ch: str = "─") -> str:
  return ch * _term_width()


async def run_gemcode_scrollback_tui(
    *, cfg, runner, session_id: str, extra_tools=None
) -> None:
  """
  Claude-like terminal UI: NO internal scrolling, just terminal scrollback.

  - User prompt line starts with: ❯
  - Assistant/tool blocks start with: ⎿  (indented)
  - Tool calls are shown as a short "internal state" block.
  - Permission prompts are inline: type y/n at the prompt.
  """
  load_cli_environment()
  os.environ["GEMCODE_TUI_ACTIVE"] = "1"

  ansi = _Ansi(
    enabled=(
      sys.stdout.isatty()
      and os.environ.get("NO_COLOR") is None
      and os.environ.get("GEMCODE_TUI_NO_COLOR") is None
    )
  )

  console = Console(
      width=_term_width(),
      force_terminal=bool(sys.stdout.isatty()),
      no_color=not ansi.enabled,
      highlight=False,
  )
  if os.environ.get("GEMCODE_TUI_SHOW_DASHBOARD", "1").lower() in ("1", "true", "yes", "on"):
    print_welcome_dashboard(cfg, console=console)
  print_shortcuts_hint(console=console)
  print("")

  char_delay_ms = int(os.environ.get("GEMCODE_TUI_CHAR_DELAY_MS", "0") or "0")

  # Build the interactive input handler (prompt_toolkit when available, plain
  # input() otherwise).  Callables let the handler always read the *current*
  # model and session id even after /model or /clear commands.
  _current_session_id_holder = [session_id]

  input_handler = GemCodeInputHandler(
      ansi_enabled=ansi.enabled,
      get_model=lambda: getattr(cfg, "model", "gemini") or "gemini",
      get_session_id=lambda: _current_session_id_holder[0],
  )

  async def typewrite(text: str) -> None:
    if not text:
      return
    if char_delay_ms <= 0:
      sys.stdout.write(text)
      sys.stdout.flush()
      await asyncio.sleep(0)
      return
    for ch in text:
      sys.stdout.write(ch)
      sys.stdout.flush()
      await asyncio.sleep(char_delay_ms / 1000.0)

  REQUEST_CONFIRMATION_FC = _ADK_REQUEST_CONFIRMATION

  def _get_confirmation_fcs(events: list) -> list[types.FunctionCall]:
    out: list[types.FunctionCall] = []
    for ev in events:
      try:
        for fc in ev.get_function_calls() or []:
          if getattr(fc, "name", None) == _ADK_REQUEST_CONFIRMATION:
            out.append(fc)
      except Exception:
        continue
    return out

  def _extract_tool_and_hint(fc: types.FunctionCall) -> tuple[str, str]:
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

  # ── Live status indicator ─────────────────────────────────────────────────
  # spinner_active[0] = True while "⟳ Working..." is on the current line.
  # _clear_spinner() uses \r + ANSI erase to overwrite it before real output.
  spinner_active = [False]

  def _clear_spinner() -> None:
    if spinner_active[0]:
      sys.stdout.write("\r\033[K")
      sys.stdout.flush()
      spinner_active[0] = False

  def _fmt_tool_result(resp: object) -> str:
    """One-line summary of a tool execution result."""
    try:
      d = resp if isinstance(resp, dict) else {}
      # ADK may wrap the return value; try both the raw dict and a nested "result" key.
      inner = d.get("result", d)
      if not isinstance(inner, dict):
        inner = d
      err = inner.get("error") or d.get("error")
      if err:
        return f"\u2717 {str(err)[:80]}"
      exit_code = inner.get("exit_code")
      if exit_code is not None:
        icon = "\u2713" if exit_code == 0 else f"\u2717 exit {exit_code}"
        out = str(inner.get("stdout", "") or "").strip()
        first = out.split("\n")[0][:80] if out else ""
        if not first:
          first = str(inner.get("stderr", "") or "").strip().split("\n")[0][:80]
        return f"{icon}  {first}" if first else icon
      if inner.get("content") is not None:
        lines = str(inner["content"]).count("\n") + 1
        return f"\u2713 {lines} lines"
      if inner.get("files") is not None:
        return f"\u2713 {len(inner['files'])} files"
      if inner.get("matches") is not None:
        return f"\u2713 {len(inner['matches'])} matches"
      if inner.get("ok") or d.get("ok"):
        return "\u2713"
      return ""
    except Exception:
      return ""

  def _render_tool_results(ev) -> None:
    """Print a brief one-line summary when a tool finishes executing."""
    try:
      frs: list = []
      try:
        frs = ev.get_function_responses() or []
      except Exception:
        pass
      if not frs and ev.content and ev.content.parts:
        for part in ev.content.parts:
          fr = getattr(part, "function_response", None)
          if fr is not None:
            frs.append(fr)
      for fr in frs:
        name = getattr(fr, "name", "") or ""
        if name == _ADK_REQUEST_CONFIRMATION:
          continue
        _clear_spinner()
        resp = getattr(fr, "response", {}) or {}
        summary = _fmt_tool_result(resp)
        if summary:
          print(f"  ⎿    {ansi.dim}\u21b3 {summary}{ansi.reset}")
    except Exception:
      pass

  def _render_tool_calls(ev) -> None:
    _clear_spinner()
    try:
      fcs = ev.get_function_calls() or []
    except Exception:
      fcs = []
    for fc in fcs:
      name = getattr(fc, "name", "") or ""
      if name == _ADK_REQUEST_CONFIRMATION:
        continue
      extra = format_tool_call_extras(fc)
      if extra:
        print(
            f"  ⎿  {ansi.blue_tool}[tool]{ansi.reset} {ansi.bold}{name}{ansi.reset} "
            f"{ansi.dim}{extra}{ansi.reset}"
        )
      else:
        print(f"  ⎿  {ansi.blue_tool}[tool]{ansi.reset} {ansi.bold}{name}{ansi.reset}")

  run_config = (
    RunConfig(max_llm_calls=cfg.max_llm_calls)
    if getattr(cfg, "max_llm_calls", None) is not None
    else None
  )

  current_session_id = session_id

  while True:
    try:
      prompt = await input_handler.prompt_async()
    except EOFError:
      print("")
      return
    if not prompt:
      continue
    if prompt in (":q", "quit", "exit", "/exit"):
      return

    old_model = getattr(cfg, "model", "")
    old_model_overridden = bool(getattr(cfg, "model_overridden", False))

    slash = await process_repl_slash(
        cfg=cfg,
        runner=runner,
        session_id=current_session_id,
        prompt_text=prompt,
        extra_tools=extra_tools,
    )
    if slash is not None:
      if slash.exit_repl:
        return
      if slash.new_session_id is not None:
        current_session_id = slash.new_session_id
        _current_session_id_holder[0] = current_session_id
      if slash.skip_model_turn:
        # Runner binds the model at creation time (LlmAgent(model=...)),
        # so rebuild it when the user overrides the model mid-session.
        new_model = getattr(cfg, "model", "")
        new_model_overridden = bool(getattr(cfg, "model_overridden", False))
        if new_model != old_model or new_model_overridden != old_model_overridden:
          try:
            close_fn = getattr(runner, "close", None)
            if close_fn:
              maybe = close_fn()
              if asyncio.iscoroutine(maybe):
                await maybe
          except Exception:
            pass
          from gemcode.session_runtime import create_runner

          runner = create_runner(cfg, extra_tools=extra_tools)
        continue
      prompt = slash.model_prompt or prompt

    apply_capability_routing(cfg, prompt, context="prompt")
    cfg.model = pick_effective_model(cfg, prompt)

    current_message = types.Content(role="user", parts=[types.Part(text=prompt)])
    do_reset = True
    def _normalize_ws(s: str) -> str:
      # Gemini can sometimes return identical content for both "thinking" and
      # final text; normalize whitespace to detect exact duplicates.
      return " ".join((s or "").split()).strip().lower()

    while True:
      events: list = []
      assistant_wrote_text = False
      buffered_thought: list[str] = []
      buffered_final: list[str] = []
      kwargs = dict(
          user_id="local", session_id=current_session_id, new_message=current_message
      )
      if run_config is not None:
        kwargs["run_config"] = run_config
      # (We don't handle token budget reset here; full-screen TUI does.)

      # Show a live "Working…" indicator so the user never wonders if the
      # agent froze.  _clear_spinner() overwrites it with \r+ANSI-erase
      # before the first real output (tool call, tool result, or response).
      if ansi.enabled:
        spinner_active[0] = True
        sys.stdout.write(f"  {ansi.dim}\u27f3  Working\u2026{ansi.reset}")
        sys.stdout.flush()

      async for ev in runner.run_async(**kwargs):
        events.append(ev)
        _render_tool_calls(ev)
        _render_tool_results(ev)
        try:
          if not ev.content or not ev.content.parts:
            continue
          if not getattr(ev, "author", None) or ev.author == "user":
            continue
          for part in ev.content.parts:
            delta = getattr(part, "text", None)
            if not delta:
              continue
            assistant_wrote_text = True
            if getattr(part, "thought", None):
              buffered_thought.append(delta)
            else:
              buffered_final.append(delta)
        except Exception:
          continue

      _clear_spinner()  # ensure "Working…" is gone before any response line

      if not assistant_wrote_text and _events_had_non_confirmation_tools(events):
        await typewrite(
            f"{ansi.dim}(Tools ran without a text reply in this step; "
            f"the run may continue in the background. Ask a follow-up if you need more.){ansi.reset}"
        )

      confirmation_fcs = _get_confirmation_fcs(events)
      if not confirmation_fcs:
        # Render buffered thinking and final response separately.
        thought_text = "".join(buffered_thought)
        final_text = "".join(buffered_final)
        if buffered_thought:
          if buffered_final and _normalize_ws(thought_text) == _normalize_ws(final_text):
            print(
              f"  ⎿  {ansi.dim}{ansi.bold}\u2234 Thinking{ansi.reset}: "
              f"{ansi.reset}(omitted: identical to final response)"
            )
            print("")
          else:
            sys.stdout.write(
              f"  ⎿  {ansi.dim}{ansi.bold}\u2234 Thinking{ansi.reset}: "
            )
            sys.stdout.flush()
            await typewrite(thought_text)
            sys.stdout.write("\n")
            sys.stdout.flush()
        if buffered_final:
          sys.stdout.write(f"  ⎿  {ansi.bold}GemCode{ansi.reset}: ")
          sys.stdout.flush()
          await typewrite(final_text)
        break

      interactive_enabled = bool(getattr(cfg, "interactive_permission_ask", False))
      parts: list[types.Part] = []
      for fc in confirmation_fcs:
        tool_name, hint = _extract_tool_and_hint(fc)
        if not interactive_enabled:
          print("")
          print(
            f"  ⎿  {ansi.blue_warn}{ansi.bold}Permission needed{ansi.reset} for {ansi.bold}{tool_name}{ansi.reset} "
            f"but perm mode is not ask. Denying."
          )
          ok = False
        else:
          print("")
          if hint:
            print(
              f"  ⎿  {ansi.blue}{ansi.bold}Permission needed{ansi.reset} for {ansi.bold}{tool_name}{ansi.reset}: {hint}"
            )
          else:
            print(
              f"  ⎿  {ansi.blue}{ansi.bold}Permission needed{ansi.reset} for {ansi.bold}{tool_name}{ansi.reset}."
            )
          try:
            ans = input(
              f"  ⎿  Allow? ({ansi.blue_ok}y{ansi.reset}/{ansi.dim}N{ansi.reset}) "
            ).strip().lower()
          except EOFError:
            ans = ""
          ok = ans in ("y", "yes")

        parts.append(
          types.Part(
            function_response=types.FunctionResponse(
              name=REQUEST_CONFIRMATION_FC,
              id=getattr(fc, "id", None),
              response={"confirmed": bool(ok)},
            )
          )
        )
      current_message = types.Content(role="user", parts=parts)
      do_reset = False

    print("")
    if os.environ.get("GEMCODE_TUI_TURN_FOOTER", "1").lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
      sid = (
          current_session_id[:8]
          if len(current_session_id) >= 8
          else current_session_id
      )
      model = getattr(cfg, "model", "") or ""
      print(f"{ansi.dim}  · {model} · session {sid}{ansi.reset}")
    if os.environ.get("GEMCODE_TUI_TURN_RULE", "1").lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
      print(f"{ansi.dim}{_hr(ch='─')}{ansi.reset}")
    print("")

