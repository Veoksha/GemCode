"""
Shared REPL slash-command dispatcher (CLI plain REPL + scrollback TUI).

Returns ``None`` when the line is not a slash command; otherwise a
`ReplSlashResult` describing how the UI should proceed.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from gemcode.config import GemCodeConfig
from gemcode.context_warning import (
  calculate_context_warning_state,
  get_auto_compact_threshold_tokens,
  get_effective_context_window_size_tokens,
)
from gemcode.repl_commands import (
  format_audit_lines,
  format_doctor_lines,
  format_hooks_lines,
  format_memory_lines,
  format_model_lines,
  format_permissions_lines,
  format_tools_lines,
  slash_help_lines,
)
from gemcode.slash_commands import parse_slash_command


@dataclass
class ReplSlashResult:
  """How the REPL should handle this input line."""

  exit_repl: bool = False
  new_session_id: str | None = None
  skip_model_turn: bool = False
  model_prompt: str | None = None


def _parse_tail_n(args: str, *, default: int = 40) -> int:
  parts = (args or "").strip().split()
  if not parts:
    return default
  try:
    n = int(parts[0])
    return max(1, min(5000, n))
  except ValueError:
    return default


async def process_repl_slash(
    *,
    cfg: GemCodeConfig,
    runner: Any,
    session_id: str,
    prompt_text: str,
    print_fn: Callable[..., None] = print,
    extra_tools: Iterable[Any] | None = None,
) -> ReplSlashResult | None:
  sc = parse_slash_command(prompt_text)
  if sc is None:
    return None

  name = sc.command_name.lower()

  def out(*args: Any, **kwargs: Any) -> None:
    print_fn(*args, **kwargs)

  if name in ("help", "?"):
    out("\n".join(slash_help_lines()))
    out()
    return ReplSlashResult(skip_model_turn=True)

  if name == "doctor":
    out("\n".join(format_doctor_lines(cfg)))
    out()
    return ReplSlashResult(skip_model_turn=True)

  if name in ("model", "models"):
    args = (sc.args or "").strip()
    if not args:
      out("\n".join(format_model_lines(cfg)))
      out()
      return ReplSlashResult(skip_model_turn=True)

    parts = args.split()
    sub = parts[0].lower()
    if sub in ("use", "set") and len(parts) >= 2:
      new_model = " ".join(parts[1:]).strip()
      if not new_model:
        out("Usage: /model use <model-id>")
        out()
        return ReplSlashResult(skip_model_turn=True)
      # Persist override for this session; pick_effective_model() respects this.
      cfg.model = new_model
      setattr(cfg, "model_overridden", True)
      out(f"model: {cfg.model}")
      out("model_overridden: True")
      out("Note: this applies to subsequent turns in this REPL session.")
      out()
      return ReplSlashResult(skip_model_turn=True)

    # Fallback: show current routing info.
    out("\n".join(format_model_lines(cfg)))
    out("Tip: /model use <model-id> to override for this session.")
    out()
    return ReplSlashResult(skip_model_turn=True)

  if name in ("permissions", "perm", "permission"):
    out("\n".join(format_permissions_lines(cfg)))
    out()
    return ReplSlashResult(skip_model_turn=True)

  if name == "memory":
    out("\n".join(format_memory_lines(cfg)))
    out()
    return ReplSlashResult(skip_model_turn=True)

  if name == "hooks":
    out("\n".join(format_hooks_lines(cfg)))
    out()
    return ReplSlashResult(skip_model_turn=True)

  if name == "version":
    out(
        os.environ.get(
            "GEMCODE_VERSION",
            "(unset — install from package or set GEMCODE_VERSION)",
        )
    )
    out()
    return ReplSlashResult(skip_model_turn=True)

  if name == "tools":
    out("\n".join(format_tools_lines(cfg, extra_tools=extra_tools)))
    out()
    return ReplSlashResult(skip_model_turn=True)

  if name in ("audit", "logs"):
    tail = _parse_tail_n(sc.args, default=40)
    out("\n".join(format_audit_lines(cfg, tail=tail)))
    out()
    return ReplSlashResult(skip_model_turn=True)

  if name == "status":
    out(f"model: {cfg.model}")
    out(f"project_root: {cfg.project_root}")
    out(f"session_id: {session_id}")
    out(f"permission_mode: {cfg.permission_mode}")
    out(f"yes_to_all: {cfg.yes_to_all}")
    out()
    return ReplSlashResult(skip_model_turn=True)

  if name == "config":
    out("Key settings (env vars):")
    out(f"  GEMCODE_MODEL={os.environ.get('GEMCODE_MODEL', cfg.model)}")
    out(
        f"  GEMCODE_TOOL_RESULT_MAX_CHARS={os.environ.get('GEMCODE_TOOL_RESULT_MAX_CHARS', '12000')}"
    )
    out(f"  GEMCODE_MAX_CONTEXT_CHARS={os.environ.get('GEMCODE_MAX_CONTEXT_CHARS', '400000')}")
    out(f"  GEMCODE_CONTEXT_SHRINK={os.environ.get('GEMCODE_CONTEXT_SHRINK', '1')}")
    out(f"  GEMCODE_AUTOCOMPACT={os.environ.get('GEMCODE_AUTOCOMPACT', '1')}")
    out(
        f"  GEMCODE_AUTOCOMPACT_KEEP_CONTENT_ITEMS={os.environ.get('GEMCODE_AUTOCOMPACT_KEEP_CONTENT_ITEMS', '18')}"
    )
    out(
        f"  GEMCODE_AUTOCOMPACT_BUFFER_CHARS={os.environ.get('GEMCODE_AUTOCOMPACT_BUFFER_CHARS', '60000')}"
    )
    out()
    return ReplSlashResult(skip_model_turn=True)

  if name in ("session", "clear"):
    if name == "clear" or sc.args.strip().lower() in ("new", "reset"):
      new_id = str(uuid.uuid4())
      out(f"new session_id: {new_id}")
      out()
      return ReplSlashResult(skip_model_turn=True, new_session_id=new_id)
    out(f"session_id: {session_id}")
    out()
    return ReplSlashResult(skip_model_turn=True)

  if name == "context":
    try:
      sess = await runner.session_service.get_session(
          app_name="gemcode",
          user_id="local",
          session_id=session_id,
      )
    except Exception as e:
      out(f"[gemcode] could not load session: {e}")
      out()
      return ReplSlashResult(skip_model_turn=True)
    st = getattr(sess, "state", None) or {}
    last_pt = st.get("gemcode:last_prompt_tokens")
    last_pct = st.get("gemcode:last_context_percent_left")
    eff = get_effective_context_window_size_tokens(cfg.model)
    aut = get_auto_compact_threshold_tokens(cfg.model)
    out(f"model: {cfg.model}")
    out(
        f"effective_context_window_tokens≈{eff} "
        "(override with GEMCODE_CONTEXT_WINDOW_TOKENS)"
    )
    out(f"autocompact_threshold_tokens≈{aut}")
    if isinstance(last_pt, int):
      cw = calculate_context_warning_state(
          prompt_token_count=last_pt, model=cfg.model, cfg=cfg
      )
      out(f"last_prompt_token_count: {last_pt}")
      out(f"estimated_percent_left: {cw.get('percent_left')}%")
      out(
          "flags: "
          f"warning={cw.get('is_above_warning_threshold')} "
          f"error={cw.get('is_above_error_threshold')} "
          f"autocompact_zone={cw.get('is_above_auto_compact_threshold')} "
          f"blocking={cw.get('is_at_blocking_limit')}"
      )
    else:
      out("last_prompt_token_count: (not yet available — send a message first)")
      if last_pct is not None:
        out(f"last_stored_percent_left: {last_pct}")
    out()
    return ReplSlashResult(skip_model_turn=True)

  if name == "compact":
    os.environ["GEMCODE_AUTOCOMPACT_FORCE"] = "1"
    return ReplSlashResult(
        skip_model_turn=False,
        model_prompt=(
            "Compact the conversation history now. Reply with: Compacted."
        ),
    )

  if name in ("exit", "quit"):
    return ReplSlashResult(exit_repl=True)

  out(f"Unknown command: /{sc.command_name}")
  out("Try /help")
  out()
  return ReplSlashResult(skip_model_turn=True)
