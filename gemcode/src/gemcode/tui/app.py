from __future__ import annotations

import asyncio
import os
import subprocess
import time
import warnings
from datetime import datetime

from gemcode.capability_routing import apply_capability_routing
from gemcode.config import load_cli_environment
from gemcode.model_routing import pick_effective_model
from gemcode.repl_slash import process_repl_slash
from gemcode.tui.scrollback import format_tool_call_extras
from gemcode.version import get_version


async def run_gemcode_tui(
    *, cfg, runner, session_id: str, extra_tools=None
) -> None:
  """
  Minimal full-screen TUI using Prompt Toolkit:
  - Header: status + key hints
  - Body: scrollback
  - Footer: fixed multi-line input

  This intentionally focuses on Claude-like *interaction ergonomics* first.

  ``extra_tools`` matches the runner (e.g. MCP toolsets when ``--mcp``) so
  ``/tools`` lists the same inventory as the agent.
  """
  load_cli_environment()
  session_state = {"id": session_id}

  from prompt_toolkit.application import Application
  from prompt_toolkit.key_binding import KeyBindings
  from prompt_toolkit.layout import Dimension as D
  from prompt_toolkit.layout import Layout
  from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
  from prompt_toolkit.layout.controls import FormattedTextControl
  from prompt_toolkit.filters import Condition
  from prompt_toolkit.styles import Style
  from prompt_toolkit.widgets import Frame, TextArea
  from google.adk.agents.run_config import RunConfig
  from google.genai import types

  # Signal other parts of GemCode (callbacks) that a full-screen TUI is active.
  # Prevents stray stderr prints from corrupting the alternate screen.
  os.environ["GEMCODE_TUI_ACTIVE"] = "1"

  # Some upstream libraries emit noisy warnings to stderr which can corrupt a TUI.
  warnings.filterwarnings(
    "ignore",
    message=r"^Warning: there are non-text parts in the response: .*",
    category=UserWarning,
  )

  # Note: we need to append streaming text into this buffer; Prompt Toolkit
  # raises EditReadOnlyBuffer if we try to insert into a read-only buffer.
  # Keep it unfocusable so the user can't type into it.
  output = TextArea(
    text="",
    read_only=False,
    scrollbar=True,
    focusable=False,
    wrap_lines=True,
    # Critical: keep transcript in its own scrollable pane.
    # Without this, TextArea can grow with content and overlap the input panel.
    height=D(weight=1),
  )
  input_box = TextArea(
    prompt="❯ ",
    multiline=True,
    wrap_lines=True,
    height=D(min=3, max=6, preferred=3),
  )

  interrupted = {"flag": False}

  def append(text: str) -> None:
    output.buffer.insert_text(text)
    if not text.endswith("\n"):
      output.buffer.insert_text("\n")
    output.buffer.cursor_position = len(output.text)
    # Force a redraw; some terminals won't repaint correctly until resize.
    try:
      app.invalidate()
    except Exception:
      pass

  def append_inline(text: str) -> None:
    """Append without forcing a newline (for streaming deltas)."""
    output.buffer.insert_text(text)
    output.buffer.cursor_position = len(output.text)
    try:
      app.invalidate()
    except Exception:
      pass

  # Character-by-character rendering (Claude-like feel), even if upstream deltas
  # arrive as full sentences.
  #
  # - GEMCODE_TUI_CHAR_DELAY_MS: per-character delay (default 0ms)
  # - GEMCODE_TUI_CHAR_YIELD_EVERY: yield to event loop after N chars (default 1)
  _delay_ms = int(os.environ.get("GEMCODE_TUI_CHAR_DELAY_MS", "0") or "0")
  _yield_every = max(1, int(os.environ.get("GEMCODE_TUI_CHAR_YIELD_EVERY", "1") or "1"))

  async def typewrite(text: str) -> None:
    if not text:
      return
    n = 0
    for ch in text:
      append_inline(ch)
      n += 1
      # Force a render tick.
      try:
        app.invalidate()
      except Exception:
        pass
      if _delay_ms > 0:
        await asyncio.sleep(_delay_ms / 1000.0)
      elif n % _yield_every == 0:
        await asyncio.sleep(0)

  def header_text():
    model = getattr(cfg, "model", "") or ""
    mode = (
      "yes"
      if getattr(cfg, "yes_to_all", False)
      else "ask"
      if getattr(cfg, "interactive_permission_ask", False)
      else "ro"
    )
    root = str(getattr(cfg, "project_root", "") or "")
    now = datetime.now().strftime("%a %b %d %H:%M")
    # Shift+Enter isn't reliably distinguishable across terminals, so we
    # provide a portable newline binding (Ctrl+J).
    tips = "Enter=send | Ctrl+J=newline | Esc=interrupt | Ctrl+D=exit"
    return [
      ("class:brand", " GemCode "),
      ("", f"  model={model or '<auto>'}  perm={mode}  root={root}  {now}\n"),
      ("class:muted", f" {tips}"),
    ]

  header = Window(height=2, content=FormattedTextControl(header_text), dont_extend_height=True)

  _git_cache = {"t": 0.0, "branch": ""}

  def _git_branch() -> str:
    # Claude shows git branch in status line; do a tiny cached call here.
    now = time.time()
    if now - _git_cache["t"] < 5 and _git_cache["branch"]:
      return _git_cache["branch"]
    try:
      p = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(getattr(cfg, "project_root", "") or "."),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=0.15,
      )
      b = (p.stdout or "").strip()
      if b and b != "HEAD":
        _git_cache["branch"] = b
        _git_cache["t"] = now
        return b
    except Exception:
      pass
    _git_cache["t"] = now
    _git_cache["branch"] = ""
    return ""

  status = Window(
    height=1,
    dont_extend_height=True,
    content=FormattedTextControl(lambda: [("class:muted", " loading… ")]),
  )

  # Non-modal permission prompt state. Modal dialogs can corrupt a full-screen TUI.
  pending_confirm: dict[str, object] = {"future": None, "tool": "", "hint": ""}
  assistant_busy: dict[str, bool] = {"value": False}
  spinner_idx: dict[str, int] = {"value": 0}

  def _set_input_prompt() -> None:
    if pending_confirm.get("future") is not None:
      input_box.prompt = "⎿ perm "
    else:
      input_box.prompt = "❯ "

  def _input_help_text():
    if pending_confirm.get("future") is not None:
      tool = str(pending_confirm.get("tool") or "tool")
      return [
        ("class:muted", " "),
        ("class:accent", f"Permission needed for "),
        ("class:pill", tool),
        ("class:muted", ". Type "),
        ("class:accent", "y"),
        ("class:muted", " or "),
        ("class:accent", "n"),
        ("class:muted", " in the input below and press Enter."),
      ]
    return [
      ("class:muted", " "),
      ("class:muted", "Type your message below. Enter=send · Ctrl+J=newline · Ctrl+O=home"),
    ]

  def _status_text():
    fut = pending_confirm.get("future")
    if fut is not None:
      tool = str(pending_confirm.get("tool") or "tool")
      return [
        ("class:muted", " "),
        ("class:pill", f"Permission: {tool}"),
        ("class:muted", "  "),
        ("class:accent", "y=approve"),
        ("class:muted", "  "),
        ("class:accent", "n=deny"),
        ("class:muted", "  "),
        ("class:muted", "(Esc cancels)"),
      ]
    if assistant_busy.get("value"):
      frames = ["|", "/", "-", "\\"]
      fr = frames[spinner_idx.get("value", 0) % len(frames)]
      return [
        ("class:muted", " "),
        ("class:pill", f"thinking {fr}"),
        ("class:muted", "  "),
        ("class:muted", "Tip: Esc=interrupt"),
      ]
    return [
      ("class:muted", " "),
      ("class:pill", f"🌿 {_git_branch()}" if _git_branch() else "📁 no-git"),
      ("class:muted", "  "),
      ("class:muted", "Tip: Ctrl+J=newline  Esc=interrupt  Ctrl+D=exit"),
    ]

  status.content = FormattedTextControl(_status_text)
  _set_input_prompt()

  async def _spin_status() -> None:
    frames = ["|", "/", "-", "\\"]
    i = 0
    while assistant_busy.get("value"):
      spinner_idx["value"] = i % len(frames)
      i += 1
      try:
        app.invalidate()
      except Exception:
        pass
      await asyncio.sleep(0.12)

  input_help = Window(
    height=1,
    dont_extend_height=True,
    content=FormattedTextControl(_input_help_text),
  )

  # Home dashboard behavior:
  # - Default: stay visible until user toggles it off (more like Claude's home screen)
  # - Optional: hide on first send via GEMCODE_TUI_HOME_HIDE_ON_SEND=1
  show_home = {"value": True}
  hide_home_on_send = os.environ.get("GEMCODE_TUI_HOME_HIDE_ON_SEND", "0").lower() in (
    "1",
    "true",
    "yes",
    "on",
  )

  def _uname() -> str:
    for k in ("USER", "LOGNAME"):
      v = (os.environ.get(k) or "").strip()
      if v:
        return v
    return "there"

  def _model_display() -> str:
    m = getattr(cfg, "model", "") or ""
    if not m:
      return "GemCode"
    return m.replace("gemini-", "Gemini ").replace("-", ".")

  def _render_home_text():
    # Recompute with current terminal width for a "dashboard" feel.
    cols = 80
    rows = 24
    try:
      cols = app.output.get_size().columns
      rows = app.output.get_size().rows
    except Exception:
      pass
    width = max(60, min(cols - 2, 120))
    left_w = (width - 4) * 2 // 3
    right_w = (width - 4) - left_w

    def pad(s: str, w: int) -> str:
      if len(s) > w:
        return s[: max(0, w - 1)] + "…"
      return s + (" " * (w - len(s)))

    mid_title = "│" + pad(
        f" GemCode  v{os.environ.get('GEMCODE_VERSION', get_version())}",
        width - 2,
    ) + "│"

    welcome = f"Welcome back {_uname()}!"
    bot = [
      f"{_model_display()}  ·  Local session  ·  {str(getattr(cfg, 'project_root', '') or '')}",
    ]

    # Tiny "gem" ASCII mark (kept simple for terminal portability).
    art = [
      "   ▄▄▄▄   ",
      "  ▐█  █▌  ",
      "  ▐█  █▌  ",
      "   ▀▀▀▀   ",
    ]
    art_w = max(len(x) for x in art)

    left_lines: list[str] = []
    left_lines.append("")
    left_lines.append(" " + welcome)
    left_lines.append("")
    # Center the art in left pane.
    left_lines.append(" " * ((left_w - art_w) // 2) + art[0])
    left_lines.append(" " * ((left_w - art_w) // 2) + art[1])
    left_lines.append(" " * ((left_w - art_w) // 2) + art[2])
    left_lines.append(" " * ((left_w - art_w) // 2) + art[3])
    left_lines.append("")
    for b in bot:
      left_lines.append(" " + b)

    tips = [
      "Tips for getting started",
      "First run: trust folder, API key, then .gemcode/",
      "Note: Use perm=ask to approve tools",
    ]
    activity = ["Recent activity", "No recent activity"]

    right_lines: list[str] = []
    right_lines.append("")
    right_lines.extend([f" {tips[0]}", f"  {tips[1]}", f"  {tips[2]}"])
    right_lines.append("")
    right_lines.extend([f" {activity[0]}", f"  {activity[1]}"])

    # Normalize heights
    h = max(len(left_lines), len(right_lines))
    left_lines += [""] * (h - len(left_lines))
    right_lines += [""] * (h - len(right_lines))

    lines: list[str] = []
    lines.append(mid_title)
    lines.append("│" + (" " * (width - 2)) + "│")
    for i in range(h):
      l = pad(left_lines[i], left_w)
      r = pad(right_lines[i], right_w)
      lines.append("│ " + l + " │ " + r + " │")
    lines.append("│" + (" " * (width - 2)) + "│")
    lines.append("└" + ("─" * (width - 2)) + "┘")
    lines.append("↑ GemCode Pro now supports larger contexts · faster streaming")
    lines.append("")
    lines.append("  ? for shortcuts".ljust(max(0, width - 12)) + "Ctrl+O home")

    # Prevent overflow: clamp to available rows (leave space for header/input/status).
    max_lines = max(6, min(len(lines), max(6, rows - 7)))
    lines = lines[:max_lines]

    # Return as formatted text with subtle coloring.
    out = []
    for ln in lines:
      if "GemCode  v" in ln:
        out.append(("class:brand", ln + "\n"))
      elif "Tips for getting started" in ln or "Recent activity" in ln:
        out.append(("class:accent", ln + "\n"))
      else:
        out.append(("", ln + "\n"))
    return out

  home = ConditionalContainer(
    content=Window(
      # Allow the home dashboard to shrink on small terminals.
      height=D(min=6, max=16, preferred=16),
      dont_extend_height=True,
      content=FormattedTextControl(_render_home_text),
    ),
    filter=Condition(lambda: bool(show_home["value"])),
  )

  kb = KeyBindings()

  @kb.add("c-d")
  def _exit(event) -> None:
    event.app.exit()

  @kb.add("escape")
  def _interrupt(event) -> None:
    # If awaiting permission, Esc denies (keeps UI stable).
    fut = pending_confirm.get("future")
    if fut is not None and hasattr(fut, "done") and not fut.done():  # type: ignore[attr-defined]
      try:
        fut.set_result(False)  # type: ignore[union-attr]
      except Exception:
        pass
      pending_confirm["future"] = None
      try:
        event.app.invalidate()
      except Exception:
        pass
      return
    interrupted["flag"] = True
    append("\n[interrupt] (best-effort) cancelling current turn…\n")

  # Note: do NOT bind y/n globally. Permission answers are typed into the
  # input field (perm>) and submitted with Enter, Claude-style.
  @kb.add("c-o")
  def _toggle_home(event) -> None:
    show_home["value"] = not show_home["value"]
    try:
      event.app.invalidate()
    except Exception:
      pass

  @kb.add("c-j")
  def _newline(event) -> None:
    input_box.buffer.insert_text("\n")

  def _scroll_output(lines: int) -> None:
    """
    Scroll the transcript pane without changing focus.
    Positive = down, Negative = up.
    """
    try:
      # In many terminals PgUp/PgDn never reaches the app, so we also bind
      # Alt+Up/Down. Clamp to 0 to avoid weird negative scroll states.
      output.window.vertical_scroll = max(0, output.window.vertical_scroll + int(lines))
    except Exception:
      pass
    try:
      app.invalidate()
    except Exception:
      pass

  @kb.add("pageup")
  def _page_up(event) -> None:
    _scroll_output(-10)

  @kb.add("pagedown")
  def _page_down(event) -> None:
    _scroll_output(10)

  @kb.add("c-up")
  def _scroll_up(event) -> None:
    _scroll_output(-3)

  @kb.add("c-down")
  def _scroll_down(event) -> None:
    _scroll_output(3)

  # VS Code terminal reliably forwards these.
  @kb.add("escape", "up")
  def _alt_up(event) -> None:
    _scroll_output(-3)

  @kb.add("escape", "down")
  def _alt_down(event) -> None:
    _scroll_output(3)

  @kb.add("escape", "pageup")
  def _alt_page_up(event) -> None:
    _scroll_output(-10)

  @kb.add("escape", "pagedown")
  def _alt_page_down(event) -> None:
    _scroll_output(10)

  async def _send_current() -> None:
    nonlocal runner
    old_model = getattr(cfg, "model", "")
    old_model_overridden = bool(getattr(cfg, "model_overridden", False))
    prompt = (input_box.text or "").strip()
    input_box.text = ""
    input_box.buffer.cursor_position = 0
    if not prompt:
      return

    # If a permission confirmation is pending, interpret user input as the answer
    # (Claude-like: user types y/n in the main input line).
    fut = pending_confirm.get("future")
    if fut is not None and hasattr(fut, "done") and not fut.done():  # type: ignore[attr-defined]
      ans = prompt.strip().lower()
      ok = ans in ("y", "yes")
      deny = ans in ("n", "no", "")
      if ok or deny:
        # Echo the user's permission answer so it doesn't feel like input vanished.
        append(f"\nperm> {prompt}\n")
        try:
          fut.set_result(bool(ok))  # type: ignore[union-attr]
        except Exception:
          pass
        pending_confirm["future"] = None
        _set_input_prompt()
        try:
          app.invalidate()
        except Exception:
          pass
        return
      # If user typed something else, keep waiting; show a hint inline.
      _box("permission", ["Please answer with y/yes or n/no."])
      return

    interrupted["flag"] = False
    if hide_home_on_send:
      show_home["value"] = False
    append(f"\nYou: {prompt}\n")

    def slash_print(*args: object, **kwargs: object) -> None:
      sep = str(kwargs.get("sep", " "))
      end = str(kwargs.get("end", "\n"))
      text = sep.join(str(a) for a in args) + end
      output.buffer.insert_text(text)
      output.buffer.cursor_position = len(output.text)
      try:
        app.invalidate()
      except Exception:
        pass

    slash = await process_repl_slash(
        cfg=cfg,
        runner=runner,
        session_id=session_state["id"],
        prompt_text=prompt,
        print_fn=slash_print,
        extra_tools=extra_tools,
    )
    if slash is not None:
      if slash.exit_repl:
        try:
          app.exit()
        except Exception:
          pass
        return
      if slash.new_session_id is not None:
        session_state["id"] = slash.new_session_id
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
          try:
            app.invalidate()
          except Exception:
            pass
        return
      prompt = slash.model_prompt or prompt

    apply_capability_routing(cfg, prompt, context="prompt")
    cfg.model = pick_effective_model(cfg, prompt)

    assistant_busy["value"] = True
    spinner_task = asyncio.create_task(_spin_status())

    try:
      REQUEST_CONFIRMATION_FC = "adk_request_confirmation"
      # Terminal width for stable box rendering.
      try:
        cols = app.output.get_size().columns
      except Exception:
        cols = 80
      box_inner = max(30, min(cols - 4, 100))

      def _box(top_label: str, body_lines: list[str]) -> None:
        inner = box_inner
        label = f" {top_label} "
        top = "┌" + label + ("─" * max(0, inner - len(label))) + "┐"
        bot = "└" + ("─" * inner) + "┘"
        append(top)
        for ln in body_lines:
          ln = (ln or "").replace("\n", " ")
          if len(ln) > inner:
            ln = ln[: max(0, inner - 1)] + "…"
          append("│" + ln.ljust(inner) + "│")
        append(bot)

      def _get_confirmation_fcs(events: list) -> list[types.FunctionCall]:
        out: list[types.FunctionCall] = []
        for ev in events:
          try:
            for fc in ev.get_function_calls() or []:
              if getattr(fc, "name", None) == REQUEST_CONFIRMATION_FC:
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

      def _render_tool_calls(ev) -> None:
        try:
          fcs = ev.get_function_calls() or []
        except Exception:
          fcs = []
        for fc in fcs:
          name = getattr(fc, "name", "") or ""
          if name == REQUEST_CONFIRMATION_FC:
            continue
          extra = format_tool_call_extras(fc)
          if extra:
            _box("tool", [name, extra])
          else:
            _box("tool", [name])

      # Token-budget reset matches invoke.run_turn behavior.
      state_delta = None
      if getattr(cfg, "token_budget", None):
        from gemcode.config import token_budget_invocation_reset

        state_delta = token_budget_invocation_reset()

      run_config = (
        RunConfig(max_llm_calls=cfg.max_llm_calls)
        if getattr(cfg, "max_llm_calls", None) is not None
        else None
      )

      current_message = types.Content(role="user", parts=[types.Part(text=prompt)])
      do_reset = True

      assistant_started = False

      def _normalize_ws(s: str) -> str:
        # For Gemini, "thinking" and final text can sometimes be identical.
        # Normalize whitespace so we can detect exact duplicates robustly.
        return " ".join((s or "").split()).strip().lower()

      while True:
        # Stream events from ADK runner.
        events: list = []
        # Buffer assistant text for this pass.
        # Claude differentiates "thinking" from the final response, and we
        # also do that here by routing streamed parts with `part.thought=True`
        # into a separate buffer.
        buffered_thought: list[str] = []
        buffered_final: list[str] = []
        # Show Claude-like "thinking" section immediately.
        # We fill the thought content at the end of the pass (and can omit
        # identical-thought/final cases), but the label itself should appear
        # right away so there's a visible loading cue.
        append_inline("⎿ GemCode (thinking): ")
        kwargs = dict(
            user_id="local",
            session_id=session_state["id"],
            new_message=current_message,
        )
        if run_config is not None:
          kwargs["run_config"] = run_config
        if do_reset and state_delta is not None:
          kwargs["state_delta"] = state_delta

        async for ev in runner.run_async(**kwargs):
          events.append(ev)
          if interrupted["flag"]:
            # Best-effort: stop rendering more output; runner may still finish in background.
            continue

          _render_tool_calls(ev)

          # Stream assistant text deltas as they arrive.
          try:
            if not ev.content or not ev.content.parts:
              continue
            if not getattr(ev, "author", None) or ev.author == "user":
              continue
            for part in ev.content.parts:
              delta = getattr(part, "text", None)
              if not delta:
                continue
              assistant_started = True
              if getattr(part, "thought", None):
                buffered_thought.append(delta)
              else:
                buffered_final.append(delta)
          except Exception:
            continue

        if interrupted["flag"]:
          append("\n[interrupt] Turn interrupted (best-effort).\n")
          return

        # Handle in-TUI tool confirmations (HITL) Claude-style.
        confirmation_fcs = _get_confirmation_fcs(events)
        if not confirmation_fcs:
          # Now that we know no confirmation is needed, render buffered
          # thinking + final response separately.
          thought_text = "".join(buffered_thought)
          final_text = "".join(buffered_final)
          if buffered_thought:
            # If Gemini returns the same content for both "thought" and
            # final text, don't repeat it (Claude typically doesn't).
            if buffered_final and _normalize_ws(thought_text) == _normalize_ws(final_text):
              await typewrite("(omitted: identical to final response)")
              append("")
            else:
              await typewrite(thought_text)
              # Ensure visual separation before the final response section.
              append("")
          else:
            await typewrite("(no thinking output)")
            append("")
          if buffered_final:
            append_inline("⎿ GemCode: ")
            await typewrite("".join(buffered_final))
          break

        interactive_enabled = bool(getattr(cfg, "interactive_permission_ask", False))
        parts: list[types.Part] = []
        await typewrite("(thinking paused — tool confirmation requested)")
        append("")  # newline after paused thinking label
        for fc in confirmation_fcs:
          tool_name, hint = _extract_tool_and_hint(fc)
          if interactive_enabled:
            msg = f"Approve tool call '{tool_name}'?"
            if hint:
              msg += f"\n\nHint:\n{hint}"
            # Also echo a compact card in the transcript for clarity.
            _box("permission", [f"Approve: {tool_name}", (hint or "").strip()])
            fut = asyncio.get_running_loop().create_future()
            pending_confirm["future"] = fut
            pending_confirm["tool"] = tool_name
            pending_confirm["hint"] = hint
            _set_input_prompt()
            try:
              app.invalidate()
            except Exception:
              pass
            ok = bool(await fut)
            _set_input_prompt()
          else:
            ok = False
            _box(
              "permission",
              [
                f"Blocked: {tool_name}",
                "Permission mode is not 'ask' (use --interactive-ask or choose perm=ask).",
              ],
            )

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

      if not assistant_started:
        append_inline("(no text output)")
      append("")  # newline after assistant turn
      if os.environ.get("GEMCODE_TUI_TURN_FOOTER", "1").lower() in (
          "1",
          "true",
          "yes",
          "on",
      ):
        sid = session_state["id"]
        sid_short = sid[:8] if len(sid) >= 8 else sid
        model = getattr(cfg, "model", "") or ""
        append(f"\033[2m · {model} · session {sid_short}\033[0m")
      if os.environ.get("GEMCODE_TUI_TURN_RULE", "1").lower() in (
          "1",
          "true",
          "yes",
          "on",
      ):
        try:
          cw = app.output.get_size().columns
        except Exception:
          cw = 80
        append("\033[2m" + ("─" * max(40, min(cw - 2, 200))) + "\033[0m")
    except Exception as e:
      append(f"GemCode: error: {e}\n")
    finally:
      assistant_busy["value"] = False
      try:
        spinner_task.cancel()
      except Exception:
        pass

  @kb.add("enter")
  def _enter(event) -> None:
    # Enter always sends (Claude-like). Use Ctrl+J for newlines.
    event.app.create_background_task(_send_current())

  root_container = HSplit(
    [
      header,
      Window(height=1, char="-", style="class:sep"),
      home,
      output,
      Window(height=1, char="-", style="class:sep"),
      input_help,
      Frame(
        input_box,
        title=lambda: " Input (permission)" if pending_confirm.get("future") is not None else " Input ",
        style="class:inputframe",
      ),
      status,
    ]
  )

  style = Style.from_dict(
    {
      "brand": "bold #60a5fa",
      "accent": "bold #3b82f6",
      "muted": "#6b7280",
      "sep": "#1f2937",
      "pill": "bold #93c5fd",
      "inputframe": "bg:#071426 #e5e7eb",
    }
  )

  app = Application(
    layout=Layout(root_container, focused_element=input_box),
    key_bindings=kb,
    style=style,
    full_screen=True,
    mouse_support=True,
    # Keep repainting (Ink-like). Prevents input frame artifacts mid-tool-run.
    refresh_interval=0.05,
  )

  append("GemCode TUI ready. Type your prompt and press Enter.\n")
  await app.run_async()

