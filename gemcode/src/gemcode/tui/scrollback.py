from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass

from google.adk.agents.run_config import RunConfig
from google.genai import types

from gemcode.capability_routing import apply_capability_routing
from gemcode.config import load_cli_environment
from gemcode.model_routing import pick_effective_model
from gemcode.repl_slash import process_repl_slash
from gemcode.version import get_version
from gemcode.workspace_hints import narrow_workspace_tip

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
    # ANSI 256-color bright-ish blue.
    return self.esc("38;5;75")

  @property
  def blue2(self) -> str:
    # Slightly deeper blue for secondary accents.
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


def _dashboard(cfg) -> str:
  w = _term_width()
  title = f" GemCode v{os.environ.get('GEMCODE_VERSION', get_version())} "
  left_w = (w - 4) * 2 // 3
  right_w = (w - 4) - left_w

  def pad(s: str, ww: int) -> str:
    s = s.replace("\n", " ")
    if len(s) > ww:
      return s[: ww - 1] + "…"
    return s + (" " * (ww - len(s)))

  user = (os.environ.get("USER") or os.environ.get("LOGNAME") or "there").strip()
  model = getattr(cfg, "model", "") or ""
  root = str(getattr(cfg, "project_root", "") or "")

  box_top = "╭" + ("─" * (w - 2)) + "╮"
  box_bot = "╰" + ("─" * (w - 2)) + "╯"
  lines: list[str] = [box_top]
  lines.append("│" + pad(title, w - 2) + "│")
  lines.append("│" + (" " * (w - 2)) + "│")
  left = [
    "",
    f"Welcome back {user}!",
    "",
    "   ▐▛███▜▌",
    "  ▝▜█████▛▘",
    "    ▘▘ ▝▝",
    "",
    f"{model or 'GemCode'} · Local session",
    root,
  ]
  right = [
    "Tips for getting started",
    "First run creates .gemcode/ (trust + API key)",
    "",
    "Recent activity",
    "No recent activity",
  ]
  h = max(len(left), len(right))
  left += [""] * (h - len(left))
  right += [""] * (h - len(right))
  for i in range(h):
    lines.append(
      "│ " + pad(left[i], left_w) + " │ " + pad(right[i], right_w) + " │"
    )
  nt = narrow_workspace_tip(getattr(cfg, "project_root"))
  if nt:
    lines.append("│" + pad(f" {nt}", w - 2) + "│")
  lines.append(box_bot)
  lines.append("")
  lines.append("  ↑ GemCode Pro now supports larger contexts · faster streaming")
  lines.append("")
  return "\n".join(lines)


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

  if os.environ.get("GEMCODE_TUI_SHOW_DASHBOARD", "1").lower() in ("1", "true", "yes", "on"):
    dash = _dashboard(cfg)
    if ansi.enabled:
      # Color title + the ASCII mark.
      lines = dash.splitlines()
      if len(lines) >= 2:
        lines[1] = (
          lines[1]
          .replace("GemCode", f"{ansi.blue}{ansi.bold}GemCode{ansi.reset}")
          .replace("v", f"{ansi.dim}v{ansi.reset}")
        )
      for i, ln in enumerate(lines):
        if "▐▛███▜▌" in ln or "▝▜█████▛▘" in ln or "▘▘ ▝▝" in ln:
          lines[i] = f"{ansi.blue2}{ln}{ansi.reset}"
      dash = "\n".join(lines)
    print(dash)

  print(f"{ansi.dim}  ? for shortcuts{ansi.reset}")
  print("")

  char_delay_ms = int(os.environ.get("GEMCODE_TUI_CHAR_DELAY_MS", "0") or "0")

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

  def _render_tool_calls(ev) -> None:
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
      prompt = input(f"{ansi.bold}❯{ansi.reset} ").strip()
    except EOFError:
      print("")
      return
    if not prompt:
      continue
    if prompt in (":q", "quit", "exit", "/exit"):
      return

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
      if slash.skip_model_turn:
        continue
      prompt = slash.model_prompt or prompt

    apply_capability_routing(cfg, prompt, context="prompt")
    cfg.model = pick_effective_model(cfg, prompt)

    # Start streaming assistant output.
    sys.stdout.write(f"  ⎿  {ansi.bold}GemCode{ansi.reset}: ")
    sys.stdout.flush()

    current_message = types.Content(role="user", parts=[types.Part(text=prompt)])
    do_reset = True

    while True:
      events: list = []
      assistant_wrote_text = False
      kwargs = dict(
          user_id="local", session_id=current_session_id, new_message=current_message
      )
      if run_config is not None:
        kwargs["run_config"] = run_config
      # (We don't handle token budget reset here; full-screen TUI does.)

      async for ev in runner.run_async(**kwargs):
        events.append(ev)
        _render_tool_calls(ev)
        try:
          if not ev.content or not ev.content.parts:
            continue
          if not getattr(ev, "author", None) or ev.author == "user":
            continue
          for part in ev.content.parts:
            delta = getattr(part, "text", None)
            if delta:
              assistant_wrote_text = True
              await typewrite(delta)
        except Exception:
          continue

      if not assistant_wrote_text and _events_had_non_confirmation_tools(events):
        await typewrite(
            f"{ansi.dim}(Tools ran without a text reply in this step; "
            f"the run may continue in the background. Ask a follow-up if you need more.){ansi.reset}"
        )

      confirmation_fcs = _get_confirmation_fcs(events)
      if not confirmation_fcs:
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
          ans = input(
            f"  ⎿  Allow? ({ansi.blue_ok}y{ansi.reset}/{ansi.dim}N{ansi.reset}) "
          ).strip().lower()
          ok = ans in ("y", "yes")
          # Resume the assistant indent after permission prompt.
          sys.stdout.write(f"  ⎿  {ansi.bold}GemCode{ansi.reset}: ")
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

