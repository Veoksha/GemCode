from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass

from google.adk.agents.run_config import RunConfig
from google.genai import types
from rich.console import Console
from rich.markdown import Markdown as _RichMarkdown
from rich.padding import Padding as _RichPadding

from gemcode.multimodal_input import build_user_content

from gemcode.capability_routing import apply_capability_routing
from gemcode.config import load_cli_environment
from gemcode.model_routing import pick_effective_model
from gemcode.repl_slash import process_repl_slash
from gemcode.tui.input_handler import GemCodeInputHandler
from gemcode.tui.welcome_rich import print_shortcuts_hint, print_welcome_dashboard

_ADK_REQUEST_CONFIRMATION = "adk_request_confirmation"


def format_tool_call_extras(fc) -> str:
  """
  One-line summary of tool arguments for conventional ``[tool] name …`` lines.

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


def _events_to_text_best_effort(events: list) -> str:
  """Fallback extraction of assistant text from ADK events."""
  parts: list[str] = []
  for event in events or []:
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
  return "".join(parts).strip()


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


async def _read_permission_char(loop) -> bool:
  """
  Read a single character from stdin without requiring Enter.

  Uses cbreak mode (Unix) so the user just presses 'y' — no Enter needed.
  This sidesteps the prompt_toolkit raw-mode conflict entirely: after
  prompt_async() returns, the terminal may still behave as if Enter sends
  \\r instead of \\n, causing input()/readline() to block forever.
  cbreak mode + read(1) works regardless of the terminal's current line-
  discipline state.

  Falls back to readline() on Windows or non-TTY (CI, piped input).
  """
  # ── Unix / macOS: cbreak + read(1) ───────────────────────────────────────
  try:
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
      tty.setcbreak(fd)          # single-char, no echo, signals still work
      ch = await loop.run_in_executor(None, lambda: sys.stdin.read(1))
    finally:
      termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch.lower() == "y"
  except Exception:
    pass

  # ── Fallback: readline in thread (Windows, non-TTY, termios error) ───────
  try:
    raw = await loop.run_in_executor(None, sys.stdin.readline)
    return (raw or "").replace("\r", "").replace("\n", "").strip().lower() in ("y", "yes")
  except EOFError:
    return False


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
  familiar terminal UI: NO internal scrolling, just terminal scrollback.

  - User prompt line starts with: ❯
  - Assistant/tool blocks start with: ⎿  (indented)
  - Tool calls are shown as a short "internal state" block.
  - Permission prompts are inline: type y/n at the prompt.
  """
  load_cli_environment()
  os.environ["GEMCODE_TUI_ACTIVE"] = "1"

  # ── Session-start hook ──────────────────────────────────────────────────
  try:
    from gemcode.hooks import run_session_start_hook
    run_session_start_hook(cfg.project_root, model=getattr(cfg, "model", "") or "")
  except Exception:
    pass

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

  # Record session start in metadata (enables /session list, /session resume)
  try:
    from gemcode.session_store import touch_session
    touch_session(cfg.project_root, session_id)
  except Exception:
    pass

  input_handler = GemCodeInputHandler(
      ansi_enabled=ansi.enabled,
      get_model=lambda: getattr(cfg, "model", "gemini") or "gemini",
      get_session_id=lambda: _current_session_id_holder[0],
      get_cfg=lambda: cfg,
  )

  # Turn-scoped monitors/state.
  # Declared up-front so nested render helpers can update them via `nonlocal`.
  last_tool_error: dict | None = None

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
    """Return confirmation FCs from the LAST event that contains them.

    The ADK runner expects function responses only for the function calls
    in the most recent event.  Sending responses for FCs from earlier events
    in the same batch raises:
      ValueError: Last response event should only contain the responses for
        the function calls in the same function call event.
    So we scan backwards and return only the first (= last) match.
    """
    for ev in reversed(events):
      try:
        fcs = [
          fc for fc in (ev.get_function_calls() or [])
          if getattr(fc, "name", None) == _ADK_REQUEST_CONFIRMATION
        ]
        if fcs:
          return fcs
      except Exception:
        continue
    return []

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

  # ── Live animated status indicator ───────────────────────────────────────
  # An asyncio.Task drives a braille spinner that rewrites the current line
  # every 150 ms with the elapsed time.  Because the spinner runs concurrently
  # with `async for ev in runner.run_async(...)`, it updates while we await
  # the next event — giving the user live timing feedback at every phase:
  #   "Thinking…"  → model generating first response
  #   "Running…"   → tool executing (can be 30–120 s for npx / cargo / pytest)
  #   "Querying…"  → model re-querying after tool results
  _anim_task:   list = [None]   # asyncio.Task | None
  _anim_active: list = [False]  # True while spinner text is on stdout

  async def _spinner_loop(msg: str) -> None:
    frames = "⣾⣽⣻⢿⡿⣟⣯⣷"
    t0 = asyncio.get_running_loop().time()
    i = 0
    try:
      while True:
        elapsed = asyncio.get_running_loop().time() - t0
        frame = frames[i % len(frames)]
        sys.stdout.write(
            f"\r  {ansi.dim}{frame}  {msg}  ({elapsed:.0f}s){ansi.reset}  "
        )
        sys.stdout.flush()
        i += 1
        await asyncio.sleep(0.15)
    except asyncio.CancelledError:
      pass  # _stop_anim() handles clearing the line

  def _start_anim(msg: str) -> None:
    """Start (or restart) the spinner with a new status message."""
    _stop_anim()
    if not ansi.enabled:
      return
    try:
      loop = asyncio.get_event_loop()
      if loop.is_running():
        _anim_task[0] = loop.create_task(_spinner_loop(msg))
        _anim_active[0] = True
    except Exception:
      pass

  def _stop_anim() -> None:
    """Cancel the spinner task and erase its line from stdout."""
    task = _anim_task[0]
    if task is not None:
      task.cancel()
      _anim_task[0] = None
    if _anim_active[0]:
      sys.stdout.write("\r\033[K")
      sys.stdout.flush()
      _anim_active[0] = False

  def _fmt_tool_result(resp: object) -> str:
    """One-line summary of a tool execution result."""
    try:
      d = resp if isinstance(resp, dict) else {}
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
    """Show a one-line result summary; transition spinner to 'Querying…'."""
    nonlocal last_tool_error
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
      real_frs = [
          fr for fr in frs
          if getattr(fr, "name", "") not in ("", _ADK_REQUEST_CONFIRMATION)
      ]
      if not real_frs:
        return  # nothing to show; keep spinner running
      _stop_anim()  # clear "Running…" before printing results
      for fr in real_frs:
        resp = getattr(fr, "response", {}) or {}
        summary = _fmt_tool_result(resp)
        if summary:
          print(f"  ⎿    {ansi.dim}\u21b3 {summary}{ansi.reset}")
        # Capture the most recent tool error (best-effort) so we can offer to fix it.
        try:
          d = resp if isinstance(resp, dict) else {}
          inner = d.get("result", d)
          if not isinstance(inner, dict):
            inner = d
          err = inner.get("error") or d.get("error")
          exit_code = inner.get("exit_code")
          stderr = inner.get("stderr") or ""
          if err or (isinstance(exit_code, int) and exit_code != 0):
            full = ""
            if isinstance(err, str) and err.strip():
              full = err.strip()
            elif isinstance(stderr, str) and stderr.strip():
              full = stderr.strip()
            last_tool_error = {
              "tool": getattr(fr, "name", "") or "tool",
              "summary": (summary or str(err) or str(exit_code) or "error")[:120],
              "full": full[:2000],
            }
        except Exception:
          pass
      # Restart spinner while model re-queries with the tool outputs.
      _start_anim("Querying\u2026")
    except Exception:
      pass

  def _render_tool_calls(ev) -> None:
    """Print tool-call lines; transition spinner to 'Running…'."""
    try:
      fcs = ev.get_function_calls() or []
    except Exception:
      fcs = []
    real_fcs = [
        fc for fc in fcs
        if getattr(fc, "name", "") not in ("", _ADK_REQUEST_CONFIRMATION)
    ]
    if not real_fcs:
      return  # no visible tool calls; leave spinner as-is
    _stop_anim()  # clear "Thinking…" or "Querying…"
    for fc in real_fcs:
      name = getattr(fc, "name", "") or ""
      extra = format_tool_call_extras(fc)
      if extra:
        print(
            f"  ⎿  {ansi.blue_tool}[tool]{ansi.reset} {ansi.bold}{name}{ansi.reset} "
            f"{ansi.dim}{extra}{ansi.reset}"
        )
      else:
        print(f"  ⎿  {ansi.blue_tool}[tool]{ansi.reset} {ansi.bold}{name}{ansi.reset}")
    _start_anim("Running\u2026")  # spinner while tool actually executes

  run_config = (
    RunConfig(max_llm_calls=cfg.max_llm_calls)
    if getattr(cfg, "max_llm_calls", None) is not None
    else None
  )

  current_session_id = session_id
  pending_prompt: str | None = None
  last_user_prompt: str | None = None

  while True:
    if pending_prompt:
      prompt = pending_prompt
      pending_prompt = None
    else:
      try:
        prompt = await input_handler.prompt_async()
      except EOFError:
        print("")
        try:
          from gemcode.hooks import run_session_stop_hook
          run_session_stop_hook(cfg.project_root, model=getattr(cfg, "model", "") or "")
        except Exception:
          pass
        return
    if not prompt:
      continue
    if prompt in (":q", "quit", "exit", "/exit"):
      try:
        from gemcode.hooks import run_session_stop_hook
        run_session_stop_hook(cfg.project_root, model=getattr(cfg, "model", "") or "")
      except Exception:
        pass
      return

    # Plain-text resume command: rerun the last user message (useful after 503s).
    if (prompt or "").strip().lower() in ("continue", "resume", "retry", "try again", "go on"):
      if last_user_prompt:
        print(f"  ⎿  {ansi.dim}↻ Continuing last request…{ansi.reset}")
        print("")
        prompt = last_user_prompt

    old_model = getattr(cfg, "model", "")
    old_model_overridden = bool(getattr(cfg, "model_overridden", False))

    cfg.session_skill_expand_session_id = current_session_id
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
        # Touch metadata for the new/resumed session
        try:
          from gemcode.session_store import touch_session
          touch_session(cfg.project_root, current_session_id)
        except Exception:
          pass
      if slash.skip_model_turn:
        # Runner holds the LlmAgent which bakes in model + thinking config at
        # construction time.  Rebuild whenever the model or thinking changes.
        new_model = getattr(cfg, "model", "")
        new_model_overridden = bool(getattr(cfg, "model_overridden", False))
        needs_rebuild = (
            new_model != old_model
            or new_model_overridden != old_model_overridden
            or slash.force_rebuild_runner
        )
        if needs_rebuild:
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

    # Track the last real user request so "continue" can rerun it later.
    try:
      pnorm = (prompt or "").strip()
      if pnorm:
        last_user_prompt = pnorm
    except Exception:
      pass

    # Snapshot pre-turn capability state so we can detect routing-triggered changes.
    _pre_dr  = cfg.enable_deep_research
    _pre_emb = cfg.enable_embeddings
    _pre_cu  = cfg.enable_computer_use
    _pre_model = cfg.model

    apply_capability_routing(cfg, prompt, context="prompt")
    cfg.model = pick_effective_model(cfg, prompt)

    # Capabilities and model are baked into the Runner at construction time.
    # If routing changed any of them we must rebuild the runner so the new
    # tools/model are actually used for this turn.
    _routing_changed = (
        cfg.enable_deep_research != _pre_dr
        or cfg.enable_embeddings  != _pre_emb
        or cfg.enable_computer_use != _pre_cu
        or cfg.model != _pre_model
    )
    if _routing_changed:
        try:
            close_fn = getattr(runner, "close", None)
            if close_fn:
                maybe = close_fn()
                if asyncio.iscoroutine(maybe):
                    await maybe
        except Exception:
            pass
        from gemcode.session_runtime import create_runner as _create_runner_rt
        runner = _create_runner_rt(cfg, extra_tools=extra_tools)

    _attach = list(cfg.pending_attachment_paths)
    cfg.pending_attachment_paths.clear()
    if _attach:
      current_message, _attach_warn = build_user_content(
          prompt,
          _attach,
          project_root=cfg.project_root,
      )
      for w in _attach_warn:
        print(f"[gemcode] {w}", file=sys.stderr)
    else:
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
      last_tool_error = None
      kwargs = dict(
          user_id="local", session_id=current_session_id, new_message=current_message
      )
      if run_config is not None:
        kwargs["run_config"] = run_config
      # (We don't handle token budget reset here; full-screen TUI does.)

      # Animated spinner starts immediately so the user always knows the
      # agent is active.  It transitions: Thinking… → Running… → Querying…
      # as different phases of the turn complete.
      _start_anim("Thinking\u2026")

      try:
        async for ev in runner.run_async(**kwargs):
          events.append(ev)
          _render_tool_calls(ev)
          _render_tool_results(ev)
          try:
            if not ev.content or not ev.content.parts:
              continue
            # Only skip user turns. ADK often omits `author` on model events — do NOT
            # skip those or the assistant text never renders (blank reply, ↓0 tokens).
            if getattr(ev, "author", None) == "user":
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
      except Exception as _turn_err:
        # Catch runner errors (e.g. ADK ValueError from mismatched function
        # response IDs) so a single bad turn doesn't crash the whole TUI.
        _stop_anim()
        print(
            f"\n  {ansi.blue_warn}[gemcode] turn error: "
            f"{type(_turn_err).__name__}: {_turn_err}{ansi.reset}"
        )
        print(
            f"  {ansi.dim}The agent encountered an internal error on this turn. "
            f"Please send your message again.{ansi.reset}\n"
        )
        break

      _stop_anim()  # ensure spinner is gone before printing the response

      if not assistant_wrote_text and _events_had_non_confirmation_tools(events):
        await typewrite(
            f"{ansi.dim}(Tools ran without a text reply in this step; "
            f"the run may continue in the background. Ask a follow-up if you need more.){ansi.reset}"
        )

      confirmation_fcs = _get_confirmation_fcs(events)
      if not confirmation_fcs:
        thought_text = "".join(buffered_thought).strip()
        final_text   = "".join(buffered_final).strip()
        # Gemini with thinking enabled may emit visible text only in "thought" parts;
        # usage can show thoughts_token_count > 0 and candidates_token_count == 0.
        if not final_text and thought_text:
          final_text = thought_text
          thought_text = ""

        # Second-pass fallback: sometimes streamed parsing misses text (ADK event shape
        # changes, tool plugins rewriting events, etc.). Recover from the full event list.
        if not final_text:
          try:
            recovered = _events_to_text_best_effort(events)
            if recovered:
              final_text = recovered
          except Exception:
            pass

        # If the model produced no visible text at all, show an explicit hint
        # instead of returning to the prompt silently.
        if not final_text and not thought_text and not assistant_wrote_text:
          await typewrite(
            f"{ansi.dim}(No text response received. Try 'continue' to retry the last request.){ansi.reset}"
          )
          print("")

        # ── Thinking display (collapsed by default, verbose with /thinking verbose)
        if thought_text and not (
            buffered_final and _normalize_ws(thought_text) == _normalize_ws(final_text)
        ):
          show_full = bool(cfg.show_full_thinking)
          if show_full:
            # Verbose mode: full thinking rendered as Markdown, like transcript mode.
            print(f"  \u23bf  {ansi.dim}\u2234 Thinking{ansi.reset}")
            console.print(_RichPadding(_RichMarkdown(thought_text), (0, 0, 0, 4)))
            print("")
          else:
            # Collapsed: one-line excerpt + hint to expand.
            excerpt = thought_text.replace("\n", " ").strip()
            if len(excerpt) > 90:
              excerpt = excerpt[:87] + "\u2026"
            print(
                f"  \u23bf  {ansi.dim}\u2234 Thinking  {excerpt}"
                f"  \u00b7  /thinking verbose to expand{ansi.reset}"
            )
            print("")

        # ── Response — rendered as Rich Markdown ──────────────────────────
        # Pipes the text through Rich's Markdown renderer so **bold**,
        # *italic*, `code`, bullet lists and fenced code blocks all display
        # correctly instead of showing raw asterisks and backticks.
        if final_text:
          print(f"  \u23bf  {ansi.bold}GemCode{ansi.reset}:")
          console.print(
              _RichPadding(_RichMarkdown(final_text), (0, 0, 0, 4)),
          )

        # If a tool error occurred during this turn, ask whether to resolve it.
        if last_tool_error:
          try:
            tool_name = last_tool_error.get("tool") or "tool"
            summary = last_tool_error.get("summary") or "an error"
            full = last_tool_error.get("full") or ""
            print("")
            print(
              f"  ⎿  {ansi.blue_warn}{ansi.bold}Detected an error{ansi.reset} "
              f"in {ansi.bold}{tool_name}{ansi.reset}: {ansi.dim}{summary}{ansi.reset}"
            )
            sys.stdout.flush()
            prompt_str = (
              f"  ⎿  Try to resolve it now? "
              f"[{ansi.blue_ok}y{ansi.reset} = yes  "
              f"{ansi.dim}any other key = no{ansi.reset}]  "
            )
            sys.stdout.write(prompt_str)
            sys.stdout.flush()
            ok = await _read_permission_char(asyncio.get_running_loop())
            sys.stdout.write(("y" if ok else "n") + "\n")
            sys.stdout.flush()
            if ok:
              pending_prompt = (
                "We encountered an error during the last turn.\n\n"
                f"Tool: {tool_name}\n"
                f"Summary: {summary}\n\n"
                f"{full}\n\n"
                "Please fix the issue. If a command needs to be run, propose it "
                "and ask for confirmation."
              )
          except Exception:
            pass
        break

      interactive_enabled = bool(getattr(cfg, "interactive_permission_ask", False))
      parts: list[types.Part] = []
      for fc in confirmation_fcs:
        tool_name, hint = _extract_tool_and_hint(fc)
        if not interactive_enabled:
          print("")
          print(
            f"  ⎿  {ansi.blue_warn}{ansi.bold}Permission needed{ansi.reset} for "
            f"{ansi.bold}{tool_name}{ansi.reset} — auto-denying "
            f"(run with --yes or /computer on to allow)."
          )
          ok = False
        else:
          print("")
          if hint:
            print(
              f"  ⎿  {ansi.blue}{ansi.bold}Permission needed{ansi.reset} "
              f"for {ansi.bold}{tool_name}{ansi.reset}: {hint}"
            )
          else:
            print(
              f"  ⎿  {ansi.blue}{ansi.bold}Permission needed{ansi.reset} "
              f"for {ansi.bold}{tool_name}{ansi.reset}."
            )
          sys.stdout.flush()
          # The core issue: prompt_toolkit puts the terminal in raw mode
          # while reading user input. After prompt_async() returns, the
          # terminal may still be in (or close to) raw mode. In raw mode
          # pressing Enter sends \r instead of \n. Both input() and
          # readline() wait for \n — so they block forever.
          #
          # Fix: read a SINGLE CHARACTER using cbreak mode (no Enter needed).
          #   - tty.setcbreak() disables line-buffering + echo but keeps
          #     signal keys (Ctrl+C etc.) working.
          #   - read(1) returns immediately after any key is pressed.
          #   - The user only needs to press "y" — no Enter required.
          #   - On Windows (no termios) we fall back to readline().
          prompt_str = (
              f"  ⎿  Allow? "
              f"[{ansi.blue_ok}y{ansi.reset} = yes  "
              f"{ansi.dim}any other key = no{ansi.reset}]  "
          )
          sys.stdout.write(prompt_str)
          sys.stdout.flush()
          ok = await _read_permission_char(asyncio.get_running_loop())
          # Echo the answer and move to next line
          sys.stdout.write(("y" if ok else "n") + "\n")
          sys.stdout.flush()

        # Explicit visual feedback — user knows their answer was received.
        if ok:
          print(
              f"  ⎿  {ansi.blue_ok}{ansi.bold}Approved{ansi.reset} — "
              f"continuing…"
          )
        else:
          print(
              f"  ⎿  {ansi.dim}Denied — skipping {tool_name}.{ansi.reset}"
          )
        sys.stdout.flush()

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
      # Restart the spinner so the user knows the agent is working again
      # after they answered the permission prompt.
      _start_anim("Working\u2026")

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
      # ── Token / cost stats from last turn ──────────────────────────────────
      stats = getattr(cfg, "_last_turn_stats", None)
      token_part = ""
      if stats:
        try:
          from gemcode.pricing import format_cost, format_tokens
          in_tok  = stats.get("in", 0) or 0
          out_tok = stats.get("out", 0) or 0
          think_tok = stats.get("think", 0) or 0
          session_total = stats.get("session_total", 0) or 0
          turn_cost = stats.get("turn_cost")
          session_cost = stats.get("session_cost")
          parts_t: list[str] = []
          parts_t.append(f"↑{format_tokens(in_tok)} ↓{format_tokens(out_tok)}")
          if think_tok:
            parts_t.append(f"✦{format_tokens(think_tok)}")
          if turn_cost is not None:
            parts_t.append(format_cost(turn_cost))
          if session_total:
            parts_t.append(f"session {format_tokens(session_total)}")
          if session_cost and session_cost > 0.0001:
            parts_t.append(f"total {format_cost(session_cost)}")
          token_part = "  ·  " + "  ·  ".join(parts_t)
        except Exception:
          pass
      # Bottom input bar already shows model + session; avoid duplicating them here
      # (reduces visual noise and “gappy” repeats). Optional full line via env.
      _dup = os.environ.get("GEMCODE_TUI_FOOTER_DUP_STATUS", "").lower() in (
          "1", "true", "yes", "on",
      )
      if token_part:
        if _dup:
          print(f"{ansi.dim}  · {model} · session {sid}{token_part}{ansi.reset}")
        else:
          print(f"{ansi.dim}{token_part}{ansi.reset}")
      elif _dup:
        print(f"{ansi.dim}  · {model} · session {sid}{ansi.reset}")
    if os.environ.get("GEMCODE_TUI_TURN_RULE", "1").lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
      print(f"{ansi.dim}{_hr(ch='─')}{ansi.reset}")

    # ── Prompt suggestion (Openconventional stopHooks guidance) ──────────────
    # terminal_hooks_plugin stores the suggestion on cfg._last_prompt_suggestion
    # only when the turn ended with a non-"completed" terminal reason.
    try:
      suggestion = getattr(cfg, "_last_prompt_suggestion", None)
      if suggestion and isinstance(suggestion, str):
        print(f"  {ansi.blue_warn}⚑  Suggestion:{ansi.reset} {ansi.dim}{suggestion}{ansi.reset}")
        print("")
    except Exception:
      pass

    print("")

