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

  # ── /init ─────────────────────────────────────────────────────────────────
  # ── /review ───────────────────────────────────────────────────────────────
  if name == "review":
    scope = (sc.args or "").strip()
    # Build a prompt that includes the diff/files as context for the pipeline.
    # We dispatch this as a model_prompt so the main agent runs the review pipeline.
    if scope:
      review_prompt = (
        f"Run a parallel code review on: {scope}\n\n"
        "Use the review_code tool if available, or:\n"
        "1. If it's a file/directory, read the relevant files with read_file/list_directory\n"
        "2. Run `bash('git diff HEAD -- " + scope + "')` to see recent changes\n"
        "3. Produce a parallel code review using your SecurityReviewer, StyleReviewer, "
        "and CorrectnessReviewer sub-agents (via run_subtask with focus='security'/'style'/'correctness'), "
        "then synthesize the findings into a structured report with: Critical Issues, Suggestions, Verdict."
      )
    else:
      review_prompt = (
        "Run a parallel code review on the current changes.\n\n"
        "Steps:\n"
        "1. Run `bash('git diff HEAD')` to see unstaged changes, and "
        "`bash('git diff --cached')` for staged changes\n"
        "2. If no git diff, run `bash('git diff HEAD~1')` for the last commit\n"
        "3. Run THREE parallel sub-reviews using run_subtask:\n"
        "   - Security review (auth, injections, secrets, validation)\n"
        "   - Style review (readability, naming, DRY, docs)\n"
        "   - Correctness review (logic, error handling, edge cases, tests)\n"
        "4. Synthesize into a final report: Critical Issues / Suggestions / Verdict\n\n"
        "Run all three sub-reviews simultaneously using run_subtask for maximum speed."
      )
    out("Running parallel code review (security + style + correctness)…")
    out()
    return ReplSlashResult(model_prompt=review_prompt)

  # ── /notes ────────────────────────────────────────────────────────────────
  if name == "notes":
    sub = (sc.args or "").strip().lower()
    notes_path = cfg.project_root / ".gemcode" / "notes.md"
    if sub == "clear":
      if notes_path.exists():
        notes_path.unlink()
        out("Notes cleared.")
      else:
        out("No notes file found.")
      out()
      return ReplSlashResult(skip_model_turn=True)
    if sub == "edit":
      import subprocess
      editor = os.environ.get("EDITOR", "nano")
      notes_path.parent.mkdir(parents=True, exist_ok=True)
      if not notes_path.exists():
        notes_path.write_text("# GemCode Agent Notes\n*Auto-generated project notes.*\n\n", encoding="utf-8")
      subprocess.run([editor, str(notes_path)])
      return ReplSlashResult(skip_model_turn=True)
    # Default: show notes
    if notes_path.exists():
      content = notes_path.read_text(encoding="utf-8", errors="replace")
      out(f"Agent notes ({notes_path}):")
      out("─" * 60)
      out(content)
      out("─" * 60)
      out("  /notes clear   Delete all notes")
      out("  /notes edit    Open in $EDITOR")
    else:
      out(f"No agent notes yet ({notes_path}).")
      out("The agent will auto-create notes as it discovers project insights.")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /cost ─────────────────────────────────────────────────────────────────
  if name == "cost":
    from gemcode.pricing import format_cost, format_tokens
    stats = getattr(cfg, "_last_turn_stats", None)
    out("Session cost summary")
    out("─" * 40)
    if stats:
      out(f"  Last turn input tokens : {format_tokens(stats.get('in', 0) or 0)}")
      out(f"  Last turn output tokens: {format_tokens(stats.get('out', 0) or 0)}")
      think = stats.get("think", 0) or 0
      if think:
        out(f"  Last turn thinking     : {format_tokens(think)}")
      cache = stats.get("cache", 0) or 0
      if cache:
        out(f"  Last turn cache read   : {format_tokens(cache)}")
      lc = stats.get("turn_cost")
      out(f"  Last turn cost         : {format_cost(lc) if lc is not None else '(unknown model pricing)'}")
      out()
      out(f"  Session total tokens   : {format_tokens(stats.get('session_total', 0) or 0)}")
      sc = stats.get("session_cost")
      if sc and sc > 0:
        out(f"  Session total cost     : {format_cost(sc)}")
      else:
        out("  Session total cost     : (accumulating — will show after first turn)")
    else:
      out("  No turn completed yet. Send a message to see token/cost stats.")
    out()
    out(f"  Model: {getattr(cfg, 'model', 'unknown')}")
    out()
    out("  Note: costs are estimates based on published Gemini pricing.")
    out("  Thinking tokens billed at same rate as output tokens.")
    return ReplSlashResult(skip_model_turn=True)

  if name == "init":
    gemini_md = cfg.project_root / "GEMINI.md"
    if gemini_md.exists() and (sc.args or "").strip().lower() not in ("force", "overwrite", "-f"):
      out(f"GEMINI.md already exists at {gemini_md}.")
      out("Use /init force to regenerate it, or edit it manually.")
      out()
      return ReplSlashResult(skip_model_turn=True)
    # Dispatch to the model to analyze the project and write GEMINI.md.
    init_prompt = (
      "Analyze this codebase and generate a GEMINI.md file for me.\n\n"
      "To do this:\n"
      "1. Run `list_directory('.')` to understand the project structure\n"
      "2. Read `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `README.md` "
      "or equivalent to understand the project type and dependencies\n"
      "3. Look at the source directory structure (src/, lib/, app/, etc.)\n"
      "4. Check for test directories and test runner config\n"
      "5. Look for linting/formatting config files (.eslintrc, .prettierrc, ruff.toml, etc.)\n\n"
      "Then write a GEMINI.md file at the project root containing:\n"
      "# Project Name\n"
      "One-sentence description.\n\n"
      "## Build & Test\n"
      "- How to install dependencies\n"
      "- How to build\n"
      "- How to run tests\n"
      "- How to lint/format\n\n"
      "## Architecture\n"
      "- Key directories and what they contain\n"
      "- Important entry points\n"
      "- Key abstractions or patterns used\n\n"
      "## Coding standards\n"
      "- Language/framework conventions\n"
      "- Any style requirements found in config files\n\n"
      "## Workflow\n"
      "- Any git branching rules from README or CONTRIBUTING\n"
      "- PR/commit conventions\n\n"
      "Keep it under 200 lines. Write the file to GEMINI.md now."
    )
    out("Analyzing project to generate GEMINI.md…")
    out("(GemCode will read the project structure and write a starting GEMINI.md)")
    out()
    return ReplSlashResult(model_prompt=init_prompt)

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
    _bc = getattr(cfg, "_browser_computer", None)
    _bl = _bc is not None and getattr(_bc, "_page", None) is not None
    out(f"  computer_use:   {'on  ✓' if cfg.enable_computer_use else 'off'}"
        + (f"  [browser {'live' if _bl else 'ready/idle'}]" if cfg.enable_computer_use else ""))
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
    # Dynamic policy telemetry
    try:
      risk = float(getattr(cfg, "_risk_score", 0.0) or 0.0)
      pct = getattr(cfg, "_context_percent_left", None)
      out("  Dynamic policy:")
      out(f"    dynamic_token_policy:  {getattr(cfg, 'dynamic_token_policy', True)}")
      out(f"    dynamic_risk_policy:   {getattr(cfg, 'dynamic_risk_policy', True)}")
      out(f"    dynamic_risk_boost:    {getattr(cfg, 'dynamic_risk_boost', 0.6)}")
      out(f"    risk_score:            {risk:.2f}")
      if isinstance(pct, int):
        out(f"    context_percent_left:  {pct}%")
      out()
    except Exception:
      pass
    out("  Autocompact:")
    out(f"    GEMCODE_AUTOCOMPACT:               {os.environ.get('GEMCODE_AUTOCOMPACT', '1')}")
    out(f"    GEMCODE_AUTOCOMPACT_BUFFER_CHARS:  {os.environ.get('GEMCODE_AUTOCOMPACT_BUFFER_CHARS', '60000')}")
    out(f"    GEMCODE_AUTOCOMPACT_KEEP_CONTENT_ITEMS: {os.environ.get('GEMCODE_AUTOCOMPACT_KEEP_CONTENT_ITEMS', '18')}")
    out()
    return ReplSlashResult(skip_model_turn=True)

  if name in ("session", "clear"):
    args_lower = (sc.args or "").strip().lower()
    args_raw = (sc.args or "").strip()

    # /clear or /session new — start fresh session
    if name == "clear" or args_lower in ("new", "reset"):
      new_id = str(uuid.uuid4())
      out(f"new session_id: {new_id}")
      out()
      return ReplSlashResult(skip_model_turn=True, new_session_id=new_id)

    # /session list — show recent sessions
    if args_lower in ("list", "ls", "history"):
      from gemcode.session_store import list_sessions, format_session_list
      sessions = list_sessions(cfg.project_root)
      out("Recent sessions (most recent first):")
      out("─" * 55)
      for line in format_session_list(sessions):
        out(line)
      out()
      out("  /session resume <id|name>  — resume a session")
      out("  /session name <name>        — name current session")
      out()
      return ReplSlashResult(skip_model_turn=True)

    # /session name <name> — give a name to the current session
    if args_lower.startswith("name "):
      new_name = args_raw[5:].strip()
      if not new_name:
        out("Usage: /session name <name>")
        return ReplSlashResult(skip_model_turn=True)
      from gemcode.session_store import name_session
      name_session(cfg.project_root, session_id, new_name)
      out(f"Session named: '{new_name}'")
      out(f"Session id   : {session_id[:8]}…")
      out()
      return ReplSlashResult(skip_model_turn=True)

    # /session resume <id|name> — switch to a different session
    if args_lower.startswith("resume ") or args_lower.startswith("r "):
      query = args_raw.split(None, 1)[1].strip() if " " in args_raw else ""
      if not query:
        out("Usage: /session resume <session-id|name|prefix>")
        return ReplSlashResult(skip_model_turn=True)
      from gemcode.session_store import find_session
      found = find_session(cfg.project_root, query)
      if found is None:
        out(f"No session found matching '{query}'.")
        out("Use /session list to see available sessions.")
        out()
        return ReplSlashResult(skip_model_turn=True)
      if found == session_id:
        out(f"Already in session {found[:8]}.")
        out()
        return ReplSlashResult(skip_model_turn=True)
      out(f"Resuming session {found[:8]}…")
      out()
      return ReplSlashResult(skip_model_turn=True, new_session_id=found)

    # Default: show current session info
    from gemcode.session_store import get_session_name, touch_session
    touch_session(cfg.project_root, session_id)
    session_name = get_session_name(cfg.project_root, session_id)
    out(f"session_id: {session_id}")
    if session_name:
      out(f"name      : {session_name}")
    out()
    out("  /session list              — list all sessions")
    out("  /session name <name>       — name this session")
    out("  /session resume <id|name>  — resume another session")
    out("  /session new               — start a fresh session")
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
    focus = (sc.args or "").strip()
    os.environ["GEMCODE_AUTOCOMPACT_FORCE"] = "1"
    if focus:
      compact_prompt = (
        f"Compact the conversation history now. Focus on preserving: {focus}\n\n"
        "Create a concise summary that retains:\n"
        "- All decisions, conclusions, and code changes made\n"
        f"- Special attention to: {focus}\n"
        "- Any open tasks or blockers\n"
        "- Key file paths and architecture insights\n\n"
        "After summarizing, reply with a brief confirmation of what was preserved."
      )
    else:
      compact_prompt = (
        "Compact the conversation history now.\n\n"
        "Create a concise summary preserving:\n"
        "- All decisions, conclusions, and code changes made\n"
        "- Any open tasks or next steps\n"
        "- Key file paths, commands, and architecture insights\n"
        "- Error messages and their resolutions\n\n"
        "After summarizing, reply with a brief confirmation. "
        "Tip: use `/compact <focus>` to specify what to prioritize, e.g. `/compact test output and error messages`"
      )
    out("Compacting context history…")
    if focus:
      out(f"Focus: {focus}")
    out()
    return ReplSlashResult(
        skip_model_turn=False,
        model_prompt=compact_prompt,
    )

  if name in ("exit", "quit"):
    return ReplSlashResult(exit_repl=True)

  # ── /kairos ───────────────────────────────────────────────────────────────
  if name == "kairos":
    args_s = (sc.args or "").strip()
    out("Kairos — background parallel job scheduler")
    out()
    out("What it does:")
    out("  Runs a long-lived daemon that accepts prompts on stdin and executes")
    out("  each as an isolated GemCode agent job. Jobs run concurrently (up to")
    out("  --concurrency N, default 2) in a priority queue.")
    out()
    out("How to launch (in a separate terminal):")
    out(f"  gemcode kairos -C {cfg.project_root}")
    out("  gemcode kairos -C <project> --concurrency 4 --yes")
    out("  gemcode kairos -C <project> --model gemini-2.5-pro --deep-research")
    out()
    out("Options:")
    out("  --concurrency N      Max parallel jobs (default: 2)")
    out("  --default-priority N Priority for stdin jobs (higher = runs first, default: 0)")
    out("  --yes                Auto-approve mutations (like gemcode --yes)")
    out("  --model <id>         Override model for all jobs")
    out("  --model-mode <mode>  fast|balanced|quality|auto")
    out("  --deep-research      Enable Google Search + URL Context for all jobs")
    out("  --embeddings         Enable semantic file search for all jobs")
    out("  --max-llm-calls N    Cap model↔tool iterations per job")
    out("  --session <uuid>     Share session history with a running gemcode session")
    out()
    out("Tools available to jobs (in addition to normal GemCode tools):")
    out("  kairos_sleep_ms(duration_ms)   — pause this job without blocking others")
    out("  kairos_enqueue_prompt(prompt, priority, session_id)")
    out("                                  — the model can queue MORE jobs itself")
    out()
    out("Use cases:")
    out("  - Process N files in parallel: each file → one job")
    out("  - Polling loop: job sleeps, re-enqueues itself with kairos_enqueue_prompt")
    out("  - Bulk code generation, test runs, or research across many documents")
    out("  - Background work while you continue chatting in this session")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /plan ─────────────────────────────────────────────────────────────────
  if name == "plan":
    args_s = (sc.args or "").strip().lower()
    if args_s in ("on", "enable", "1", "true"):
      cfg.plan_mode = True
      out("Plan mode: ON")
      out("The agent will now write an explicit numbered plan BEFORE executing")
      out("any tools. It will pause for your confirmation before proceeding.")
      out()
      out("Type /plan off to disable.")
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    elif args_s in ("off", "disable", "0", "false"):
      cfg.plan_mode = False
      out("Plan mode: OFF")
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    else:
      status = "ON" if getattr(cfg, "plan_mode", False) else "OFF"
      out(f"Plan mode: {status}")
      out()
      out("When ON, the agent writes a numbered plan and waits for your")
      out("confirmation before executing any file or shell operations.")
      out()
      out("Best for: complex multi-file refactors, migrations, risky changes.")
      out("Toggle: /plan on   /plan off")
      return ReplSlashResult(skip_model_turn=True)

  # ── /code ─────────────────────────────────────────────────────────────────
  if name == "code":
    args_s = (sc.args or "").strip().lower()
    if args_s in ("on", "enable", "1", "true"):
      cfg.enable_code_executor = True
      out("Code executor: ON  (sandboxed Python via Gemini built-in executor)")
      out("The agent can now write Python code blocks and execute them safely,")
      out("without requiring bash/shell permission. Best for: math, data, quick tests.")
      out()
      out("Note: the sandbox has no internet/filesystem access — use bash for I/O.")
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    elif args_s in ("off", "disable", "0", "false"):
      cfg.enable_code_executor = False
      out("Code executor: OFF")
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    else:
      # Status
      status = "ON" if getattr(cfg, "enable_code_executor", False) else "OFF"
      out(f"Code executor: {status}")
      out()
      out("ADK BuiltInCodeExecutor — safe sandboxed Python execution via Gemini API.")
      out()
      out("What it does:")
      out("  When ON, the agent can write Python code blocks directly in its response")
      out("  and the Gemini API executes them in a sandboxed environment. The output")
      out("  (stdout, result) is sent back so the agent can use it for further reasoning.")
      out()
      out("Best for:")
      out("  - Math and numerical calculations")
      out("  - Data processing and transformations")
      out("  - Quick tests of logic or algorithms")
      out("  - Anything that doesn't need filesystem or network access")
      out()
      out("Not for:")
      out("  - Shell commands (use bash)")
      out("  - Reading/writing files (use read_file / write_file)")
      out("  - Internet requests (use web_fetch)")
      out()
      out("Toggle: /code on   /code off")
      out()
      out("Supported models: gemini-2.5-flash, gemini-2.5-pro, gemini-3.x and newer.")
      return ReplSlashResult(skip_model_turn=True)

  # ── /computer ────────────────────────────────────────────────────────────
  if name in ("computer", "browser"):
    args_s = (sc.args or "").strip().lower()
    enabled = bool(cfg.enable_computer_use)
    if not args_s or args_s in ("status", "show"):
      out(f"computer_use: {'on  ✓' if enabled else 'off'}")
      if enabled:
        bc = getattr(cfg, "_browser_computer", None)
        headless_env = os.environ.get("GEMCODE_COMPUTER_HEADLESS", "1").lower()
        headless = headless_env in ("1", "true", "yes", "on")
        out(f"  headless: {headless}")
        w = int(os.environ.get("GEMCODE_BROWSER_WIDTH", "1280"))
        h = int(os.environ.get("GEMCODE_BROWSER_HEIGHT", "720"))
        out(f"  viewport: {w}×{h}")
        out(f"  browser_initialized: {bc is not None and bc._page is not None}")
        if bc is not None and bc._page is not None:
          try:
            import asyncio
            url = asyncio.get_event_loop().run_until_complete(bc.get_current_url()) if not asyncio.get_event_loop().is_running() else "(running)"
          except Exception:
            url = "(check with /computer url)"
          out(f"  model_computer_use: {cfg.model_computer_use}")
        out()
        out("Available slash commands:")
        out("  /computer url     — show current browser URL")
        out("  /computer off     — disable computer use")
        out("  /computer show    — show browser window (GEMCODE_COMPUTER_HEADLESS=0 required at startup)")
      else:
        out()
        out("Enable browser automation:")
        out("  /computer on      — enable (rebuilds runner with Playwright Chromium)")
        out("  GEMCODE_COMPUTER_HEADLESS=0  — show browser window (set before start)")
        out()
        out("Requirements: pip install playwright && playwright install chromium")
      out()
      return ReplSlashResult(skip_model_turn=True)

    if args_s == "on":
      # If the session has already determined computer-use is unavailable
      # (Playwright missing), do not allow re-enabling without installing it
      # and restarting the process.
      if getattr(cfg, "_computer_use_available", True) is False:
        out("computer_use: unavailable — Playwright browsers are not installed")
        out("Run:  python3 -m playwright install chromium")
        out("Then restart GemCode and run: /computer on")
        out()
        return ReplSlashResult(skip_model_turn=True)
      cfg.enable_computer_use = True
      out("computer_use: on — Playwright Chromium browser automation enabled")
      out("  Runner will rebuild on the next turn to inject browser tools.")
      out()
      out("Browser tools available to the agent:")
      out("  navigate, click_at, type_text_at, scroll_at, key_combination,")
      out("  browser_screenshot, browser_get_text, browser_find_element, ...")
      out()
      out("Tip: set GEMCODE_COMPUTER_HEADLESS=0 to see the browser window.")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)

    if args_s == "off":
      cfg.enable_computer_use = False
      out("computer_use: off")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)

    if args_s == "url":
      bc = getattr(cfg, "_browser_computer", None)
      if bc is None or bc._page is None:
        out("Browser not initialized yet. Send a message to the agent to start it.")
      else:
        try:
          import asyncio
          if asyncio.get_event_loop().is_running():
            out("(run '/computer' to check browser status — URL read not available while async loop is running)")
          else:
            url = asyncio.get_event_loop().run_until_complete(bc.get_current_url())
            title = asyncio.get_event_loop().run_until_complete(bc.get_page_title())
            out(f"url:   {url}")
            out(f"title: {title}")
        except Exception as e:
          out(f"Error reading URL: {e}")
      out()
      return ReplSlashResult(skip_model_turn=True)

    out(f"Unknown /computer subcommand: '{args_s}'")
    out("Usage: /computer [on|off|url|status]")
    out()
    return ReplSlashResult(skip_model_turn=True)

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
    valid_caps = ("auto", "research", "embeddings", "computer", "search", "all", "none", "reset")
    out("Active capabilities:")
    out(f"  web_search:     {'on' if getattr(cfg, 'enable_web_search', False) else 'off'}")
    out(f"  deep_research:  {'on' if cfg.enable_deep_research else 'off'}")
    out(f"  embeddings:     {'on' if cfg.enable_embeddings else 'off'}")
    out(f"  memory:         {'on' if cfg.enable_memory else 'off'}")
    out(f"  computer_use:   {'on' if cfg.enable_computer_use else 'off'}")
    out(f"  maps_grounding: {'on' if cfg.enable_maps_grounding else 'off'}")
    out(f"  code_executor:  {'on' if getattr(cfg, 'enable_code_executor', False) else 'off'}")
    out(f"  plan_mode:      {'on' if getattr(cfg, 'plan_mode', False) else 'off'}")
    out(f"  capability_mode (auto-routing): {cfg.capability_mode}")
    out()
    if not args_s:
      out("Commands:")
      out("  /caps none      — turn all off, capability_mode=auto")
      out("  /caps search    — enable_web_search on (standalone google_search)")
      out("  /caps research  — enable_deep_research on (search + url_context)")
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
      if hasattr(cfg, "enable_web_search"):
        cfg.enable_web_search = False
      cfg.capability_mode = "auto"
      out("capabilities: reset to defaults (all off, auto mode)")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    if args_s == "search":
      if hasattr(cfg, "enable_web_search"):
        cfg.enable_web_search = True
      out("enable_web_search: on (google_search available without full deep_research)")
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
    if args_s in ("computer", "browser"):
      if getattr(cfg, "_computer_use_available", True) is False:
        out("enable_computer_use: unavailable — Playwright browsers are not installed")
        out("Run:  python3 -m playwright install chromium")
        out("Then restart GemCode and re-enable computer-use.")
        out()
        return ReplSlashResult(skip_model_turn=True)
      cfg.enable_computer_use = True
      out("enable_computer_use: on (runner rebuilding…)")
      out("Tip: set GEMCODE_COMPUTER_HEADLESS=0 before starting gemcode to see the browser window.")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    if args_s == "all":
      cfg.enable_deep_research = True
      cfg.enable_embeddings = True
      cfg.enable_computer_use = True
      if hasattr(cfg, "enable_web_search"):
        cfg.enable_web_search = True
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
      out("  /thinking brief    — show collapsed one-line excerpt")
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
      out("thinking display: brief — collapsed one-line excerpt")
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
