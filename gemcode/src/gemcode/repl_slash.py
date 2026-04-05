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
  force_rebuild_runner: bool = False  # True when agent config changed (thinking, etc.)


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

    if sub in ("list", "ls", "show"):
      # Best-effort list: query Gemini via the same API used by GemCode.
      show_all = "--show-all" in parts or "--show-all" in args
      try:
        from gemcode.config import load_cli_environment

        load_cli_environment()
      except Exception:
        pass
      from gemcode.cli import require_google_api_key

      require_google_api_key()
      from google.genai import Client

      client = Client(api_key=os.environ["GOOGLE_API_KEY"])
      models = client.models.list()
      out("Available models:")
      for m in models:
        name = getattr(m, "name", None)
        actions = getattr(m, "supported_actions", None)
        if not name:
          continue
        if not show_all and actions and isinstance(actions, list):
          # Keep only models that support generateContent-style generation.
          if "generateContent" not in actions:
            continue
        if actions and isinstance(actions, list):
          out(f"  {name}\t{','.join(actions)}")
        else:
          out(f"  {name}")
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
    out(f"model:          {cfg.model}")
    out(f"model_mode:     {cfg.model_mode}")
    out(f"session_id:     {session_id}")
    out(f"project_root:   {cfg.project_root}")
    out()
    out("Capabilities:")
    out(f"  deep_research:  {'on  ✓' if cfg.enable_deep_research else 'off'}")
    out(f"  embeddings:     {'on  ✓' if cfg.enable_embeddings else 'off'}")
    out(f"  memory:         {'on  ✓' if cfg.enable_memory else 'off'}")
    out(f"  computer_use:   {'on  ✓' if cfg.enable_computer_use else 'off'}")
    out(f"  maps_grounding: {'on  ✓' if cfg.enable_maps_grounding else 'off'}")
    out(f"  auto_routing:   {cfg.capability_mode}")
    out()
    out("Thinking:")
    out(f"  disabled:        {cfg.disable_thinking}")
    if cfg.thinking_level:
      out(f"  level:           {cfg.thinking_level}")
    if cfg.thinking_budget is not None:
      out(f"  budget:          {cfg.thinking_budget:,} tokens")
    out(f"  display:         {'verbose (full)' if cfg.show_full_thinking else 'brief (collapsed)'}")
    out()
    out("Permissions / limits:")
    out(f"  permission_mode: {cfg.permission_mode}")
    out(f"  yes_to_all:      {cfg.yes_to_all}")
    out(f"  max_llm_calls:   {cfg.max_llm_calls or '(SDK default)'}")
    out(f"  token_budget:    {f'{cfg.token_budget:,}' if cfg.token_budget else '(none)'}")
    out()
    return ReplSlashResult(skip_model_turn=True)

  if name == "config":
    out("Active configuration:")
    out()
    out("  Model:")
    out(f"    model:             {cfg.model}")
    out(f"    model_mode:        {cfg.model_mode}  (fast|balanced|quality|auto — /mode)")
    out(f"    model_family_mode: {cfg.model_family_mode}")
    out(f"    model_overridden:  {cfg.model_overridden}")
    out(f"    model_deep_research: {cfg.model_deep_research}")
    out()
    out("  Capabilities  (/research, /embeddings, /caps, /memory):")
    out(f"    enable_deep_research:  {cfg.enable_deep_research}")
    out(f"    enable_embeddings:     {cfg.enable_embeddings}")
    out(f"    enable_memory:         {cfg.enable_memory}")
    out(f"    enable_computer_use:   {cfg.enable_computer_use}")
    out(f"    enable_maps_grounding: {cfg.enable_maps_grounding}")
    out(f"    capability_mode:       {cfg.capability_mode}  (auto-routing)")
    out(f"    tool_combination_mode: {cfg.tool_combination_mode}")
    out()
    out("  Context / limits  (/limits, /budget):")
    out(f"    max_llm_calls:         {cfg.max_llm_calls or '(SDK default)'}")
    out(f"    max_context_chars:     {cfg.max_context_chars:,}")
    out(f"    tool_result_max_chars: {cfg.tool_result_max_chars:,}")
    out(f"    max_content_items:     {cfg.max_content_items}")
    out(f"    context_shrink:        {cfg.context_shrink_enabled}")
    out(f"    token_budget:          {f'{cfg.token_budget:,}' if cfg.token_budget else '(none)'}")
    out(f"    max_session_tokens:    {f'{cfg.max_session_tokens:,}' if cfg.max_session_tokens else '(none)'}")
    out()
    out("  Thinking  (/thinking):")
    out(f"    disable_thinking:      {cfg.disable_thinking}")
    out(f"    thinking_level:        {cfg.thinking_level or '(auto)'}")
    out(f"    thinking_budget:       {cfg.thinking_budget if cfg.thinking_budget is not None else '(auto)'}")
    out(f"    show_full_thinking:    {cfg.show_full_thinking}")
    out()
    out("  Autocompact:")
    out(f"    GEMCODE_AUTOCOMPACT:               {os.environ.get('GEMCODE_AUTOCOMPACT', '1')}")
    out(f"    GEMCODE_AUTOCOMPACT_BUFFER_CHARS:  {os.environ.get('GEMCODE_AUTOCOMPACT_BUFFER_CHARS', '60000')}")
    out(f"    GEMCODE_AUTOCOMPACT_KEEP_CONTENT_ITEMS: {os.environ.get('GEMCODE_AUTOCOMPACT_KEEP_CONTENT_ITEMS', '18')}")
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

  # ── /research ────────────────────────────────────────────────────────────
  if name == "research":
    args_s = (sc.args or "").strip().lower()
    if not args_s or args_s in ("status", "show"):
      status = "on  ✓" if cfg.enable_deep_research else "off"
      out(f"deep_research: {status}")
      if cfg.enable_deep_research:
        out(f"  model_deep_research:  {cfg.model_deep_research}")
        out(f"  enable_maps_grounding:{cfg.enable_maps_grounding}")
        out("  tools: google_search, url_context")
      out()
      out("Commands: /research on  ·  /research off")
      out("When on: Google Search + URL Context are injected as tools.")
      out("         Model switches to the deep-research routing model.")
      out()
      return ReplSlashResult(skip_model_turn=True)
    if args_s == "on":
      cfg.enable_deep_research = True
      out("research: on — Google Search + URL Context enabled")
      out("  Runner will rebuild on next turn to inject the new tools.")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    if args_s == "off":
      cfg.enable_deep_research = False
      out("research: off")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    out(f"Unknown /research subcommand: '{args_s}'")
    out("Usage: /research [on|off]")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /embeddings ──────────────────────────────────────────────────────────
  if name in ("embeddings", "embed"):
    args_s = (sc.args or "").strip().lower()
    if not args_s or args_s in ("status", "show"):
      status = "on  ✓" if cfg.enable_embeddings else "off"
      out(f"embeddings: {status}")
      if cfg.enable_embeddings:
        out(f"  embeddings_model: {cfg.embeddings_model}")
        out("  tools: semantic_search_files")
      out()
      out("Commands: /embeddings on  ·  /embeddings off")
      out("When on: semantic (meaning-based) file search via Google Embeddings API.")
      out()
      return ReplSlashResult(skip_model_turn=True)
    if args_s == "on":
      cfg.enable_embeddings = True
      out("embeddings: on — semantic_search_files tool injected")
      out("  Runner will rebuild on next turn.")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    if args_s == "off":
      cfg.enable_embeddings = False
      out("embeddings: off")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    out(f"Unknown /embeddings subcommand: '{args_s}'")
    out("Usage: /embeddings [on|off]")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /mode ─────────────────────────────────────────────────────────────────
  if name == "mode":
    args_s = (sc.args or "").strip().lower()
    valid_modes = ("fast", "balanced", "quality", "auto")
    if not args_s:
      out(f"model_mode: {cfg.model_mode}")
      out()
      out("  fast     — use the fastest model for edits and tool-heavy tasks")
      out("  balanced — moderate speed/quality (default)")
      out("  quality  — highest-quality model for architecture and complex reasoning")
      out("  auto     — GemCode picks based on prompt complexity each turn")
      out()
      out("Usage: /mode <fast|balanced|quality|auto>")
      out()
      return ReplSlashResult(skip_model_turn=True)
    if args_s in valid_modes:
      cfg.model_mode = args_s
      # Clear model_overridden so the new mode takes effect through routing
      if not getattr(cfg, "_model_explicitly_set", False):
        cfg.model_overridden = False
      out(f"model_mode: {args_s}")
      out()
      return ReplSlashResult(skip_model_turn=True)
    out(f"Unknown mode '{args_s}'. Choose from: {', '.join(valid_modes)}")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /budget ───────────────────────────────────────────────────────────────
  if name in ("budget", "token-budget"):
    args_s = (sc.args or "").strip().lower()
    if not args_s:
      tb = cfg.token_budget
      out(f"token_budget: {f'{tb:,} tokens/turn' if tb is not None else '(none — unlimited)'}")
      out()
      out("Usage: /budget <N>   Set per-turn token budget (e.g. /budget 50000)")
      out("       /budget off   Remove budget limit")
      out()
      return ReplSlashResult(skip_model_turn=True)
    if args_s == "off":
      cfg.token_budget = None
      out("token_budget: (none — unlimited)")
      out()
      return ReplSlashResult(skip_model_turn=True)
    try:
      n = int(args_s)
      if n <= 0:
        out("Token budget must be a positive integer (or 'off').")
        out()
        return ReplSlashResult(skip_model_turn=True)
      cfg.token_budget = n
      out(f"token_budget: {n:,} tokens per turn")
      out()
      return ReplSlashResult(skip_model_turn=True)
    except ValueError:
      out(f"Invalid budget '{args_s}' — use a number or 'off'.")
      out()
      return ReplSlashResult(skip_model_turn=True)

  # ── /caps ─────────────────────────────────────────────────────────────────
  if name in ("caps", "capabilities", "capability"):
    args_s = (sc.args or "").strip().lower()
    valid_caps = ("auto", "research", "embeddings", "computer", "all", "none", "reset")
    out("Active capabilities:")
    out(f"  deep_research:  {'on' if cfg.enable_deep_research else 'off'}")
    out(f"  embeddings:     {'on' if cfg.enable_embeddings else 'off'}")
    out(f"  memory:         {'on' if cfg.enable_memory else 'off'}")
    out(f"  computer_use:   {'on' if cfg.enable_computer_use else 'off'}")
    out(f"  maps_grounding: {'on' if cfg.enable_maps_grounding else 'off'}")
    out(f"  capability_mode (auto-routing): {cfg.capability_mode}")
    out()
    if not args_s:
      out("Commands:")
      out("  /caps none      — turn all off, capability_mode=auto")
      out("  /caps research  — enable_deep_research on")
      out("  /caps embeddings — enable_embeddings on")
      out("  /caps all       — all modalities on")
      out("  /caps reset     — reset to startup defaults (all off, auto mode)")
      out()
      return ReplSlashResult(skip_model_turn=True)
    if args_s in ("none", "reset"):
      cfg.enable_deep_research = False
      cfg.enable_embeddings = False
      cfg.enable_computer_use = False
      cfg.enable_maps_grounding = False
      cfg.capability_mode = "auto"
      out("capabilities: reset to defaults (all off, auto mode)")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    if args_s == "research":
      cfg.enable_deep_research = True
      out("enable_deep_research: on (runner rebuilding…)")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    if args_s == "embeddings":
      cfg.enable_embeddings = True
      out("enable_embeddings: on (runner rebuilding…)")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    if args_s == "all":
      cfg.enable_deep_research = True
      cfg.enable_embeddings = True
      cfg.enable_computer_use = True
      out("capabilities: all on (runner rebuilding…)")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    out(f"Unknown /caps value '{args_s}'. Choose from: {', '.join(valid_caps)}")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /limits ───────────────────────────────────────────────────────────────
  if name == "limits":
    args_s = (sc.args or "").strip()
    out("Current limits:")
    out(f"  max_llm_calls:         {cfg.max_llm_calls or '(SDK default)'}")
    out(f"  max_context_chars:     {cfg.max_context_chars:,}")
    out(f"  tool_result_max_chars: {cfg.tool_result_max_chars:,}")
    out(f"  max_content_items:     {cfg.max_content_items}")
    out(f"  context_shrink:        {cfg.context_shrink_enabled}")
    out(f"  token_budget:          {f'{cfg.token_budget:,}' if cfg.token_budget else '(none)'}")
    out(f"  max_session_tokens:    {f'{cfg.max_session_tokens:,}' if cfg.max_session_tokens else '(none)'}")
    out()
    if args_s:
      parts = args_s.split()
      if parts[0] == "calls" and len(parts) >= 2:
        try:
          n = int(parts[1])
          if n > 0:
            cfg.max_llm_calls = n
            out(f"max_llm_calls: {n}")
            out()
        except ValueError:
          out(f"Invalid value '{parts[1]}'")
          out()
    return ReplSlashResult(skip_model_turn=True)

  if name == "thinking":
    model_id = getattr(cfg, "model", "") or ""
    is_25 = "2.5" in model_id
    args = (sc.args or "").strip()

    if not args:
      # Show current thinking config.
      disable  = bool(cfg.disable_thinking)
      level    = cfg.thinking_level
      budget   = cfg.thinking_budget
      verbose  = bool(cfg.show_full_thinking)
      out("Thinking config:")
      out(f"  model:            {model_id or '(default)'}")
      out(f"  disable_thinking: {disable}")
      out(f"  display_mode:     {'verbose (full)' if verbose else 'brief (collapsed)'}")
      if is_25:
        out(f"  thinking_budget:  {budget if budget is not None else '(auto / dynamic)'}")
        out()
        out("Gemini 2.5 commands:")
        out("  /thinking off              — disable thinking")
        out("  /thinking on               — re-enable with auto budget")
        out("  /thinking budget <0-24576> — set exact token budget (0 = off)")
      else:
        out(f"  thinking_level:   {level if level is not None else '(auto)'}")
        out()
        out("Gemini 3.x commands:")
        out("  /thinking off                         — use minimal thinking")
        out("  /thinking on                          — re-enable auto level")
        out("  /thinking level <minimal|low|medium|high>")
      out("Display commands (all models):")
      out("  /thinking verbose  — show full thinking text each turn")
      out("  /thinking brief    — show collapsed one-line excerpt (default)")
      out()
      return ReplSlashResult(skip_model_turn=True)

    parts = args.split()
    sub = parts[0].lower()

    if sub in ("verbose", "full"):
      cfg.show_full_thinking = True
      out("thinking display: verbose — full thinking shown each turn")
      out()
      return ReplSlashResult(skip_model_turn=True)

    if sub in ("brief", "short", "collapsed"):
      cfg.show_full_thinking = False
      out("thinking display: brief — collapsed one-line excerpt (default)")
      out()
      return ReplSlashResult(skip_model_turn=True)

    if sub == "off":
      setattr(cfg, "disable_thinking", True)
      out("thinking: disabled (runner will rebuild on next turn)")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)

    if sub in ("on", "auto"):
      setattr(cfg, "disable_thinking", False)
      setattr(cfg, "thinking_level", None)
      setattr(cfg, "thinking_budget", None)
      out("thinking: auto (runner will rebuild on next turn)")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)

    if sub == "budget":
      if len(parts) < 2:
        out("Usage: /thinking budget <N>  e.g. /thinking budget 8192")
        out()
        return ReplSlashResult(skip_model_turn=True)
      try:
        budget = int(parts[1])
      except ValueError:
        out(f"Invalid budget '{parts[1]}' — must be an integer (0–24576, or -1 for dynamic)")
        out()
        return ReplSlashResult(skip_model_turn=True)
      setattr(cfg, "thinking_budget", budget)
      setattr(cfg, "disable_thinking", False)
      out(f"thinking: budget={budget} tokens (runner will rebuild on next turn)")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)

    if sub == "level":
      if len(parts) < 2:
        out("Usage: /thinking level <minimal|low|medium|high>")
        out()
        return ReplSlashResult(skip_model_turn=True)
      level = parts[1].lower()
      valid = ("minimal", "low", "medium", "high")
      if level not in valid:
        out(f"Unknown level '{level}'. Choose from: {', '.join(valid)}")
        out()
        return ReplSlashResult(skip_model_turn=True)
      setattr(cfg, "thinking_level", level)
      setattr(cfg, "disable_thinking", False)
      out(f"thinking: level={level} (runner will rebuild on next turn)")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)

    out(f"Unknown /thinking subcommand: {sub}")
    if is_25:
      out("Usage: /thinking [off | on | budget <N>]")
    else:
      out("Usage: /thinking [off | on | level <minimal|low|medium|high>]")
    out()
    return ReplSlashResult(skip_model_turn=True)

  out(f"Unknown command: /{sc.command_name}")
  out("Try /help")
  out()
  return ReplSlashResult(skip_model_turn=True)
