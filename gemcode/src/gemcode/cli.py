"""CLI entry: `gemcode "prompt"`."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import uuid
import warnings
from pathlib import Path

from gemcode.config import GemCodeConfig, load_cli_environment
from gemcode.tools_inspector import inspect_tools, smoke_tools
from gemcode.invoke import run_turn
from gemcode.model_routing import pick_effective_model
from gemcode.capability_routing import apply_capability_routing
from gemcode.session_runtime import create_runner
from gemcode.trust import is_trusted_root, trust_root
from gemcode.repl_slash import process_repl_slash


def _events_to_text(events) -> str:
  parts: list[str] = []
  for event in events:
    if not event.content or not event.content.parts:
      continue
    for part in event.content.parts:
      if part.text and getattr(event, "author", None) != "user":
        parts.append(part.text)
  return "".join(parts)


def _maybe_prompt_trust(cfg: GemCodeConfig) -> None:
  """
  Claude Code–style workspace trust prompt.

  On first use in a project root, ask the user to trust the folder so file,
  shell, and git tools can run. If not trusted, we exit before any tool runs.
  """
  # Non-interactive sessions can't answer prompts.
  if not (hasattr(sys.stdin, "isatty") and sys.stdin.isatty()):
    return
  if os.environ.get("GEMCODE_TRUST_PROMPT", "1").lower() not in ("1", "true", "yes", "on"):
    return
  root = cfg.project_root.resolve()
  if is_trusted_root(root):
    return
  try:
    print(
        "\n── GemCode setup (1/3) · Workspace permission ──\n"
        "GemCode needs full access to this workspace folder (files, shell, git):\n"
        f"  {root}\n\n"
        "Trust this folder? [y/N] ",
        file=sys.stderr,
        end="",
    )
    ans = input().strip().lower()
  except EOFError:
    raise SystemExit("Folder is not trusted; aborting.")
  if ans in ("y", "yes"):
    trust_root(root, trusted=True)
    return
  raise SystemExit("Folder is not trusted; aborting.")


def _maybe_prompt_google_api_key() -> None:
  """
  One-time interactive prompt for ``GOOGLE_API_KEY`` (like ``claude login`` / first-run).

  Skipped when the key is already set, stdin is not a TTY, or
  ``GEMCODE_NO_LOGIN_PROMPT=1``.
  """
  if os.environ.get("GOOGLE_API_KEY"):
    return
  if os.environ.get("GEMCODE_NO_LOGIN_PROMPT", "").lower() in ("1", "true", "yes", "on"):
    return
  if not (hasattr(sys.stdin, "isatty") and sys.stdin.isatty()):
    return
  if os.environ.get("GEMCODE_INTERACTIVE_LOGIN", "1").lower() not in (
    "1",
    "true",
    "yes",
    "on",
  ):
    return
  try:
    print(
        "\n── GemCode setup (2/3) · API key ──\n"
        "GemCode needs a Google Gemini API key (saved once under ~/.gemcode/).\n"
        "Create one at https://aistudio.google.com/app/apikey\n"
        "Paste your key and press Enter (input is hidden).\n",
        file=sys.stderr,
    )
    key = getpass.getpass("API key: ").strip()
  except EOFError:
    raise SystemExit("No API key provided; aborting.")
  if not key:
    raise SystemExit("No API key provided; aborting.")
  from gemcode.credentials import save_google_api_key_to_user_store

  save_google_api_key_to_user_store(key)
  os.environ["GOOGLE_API_KEY"] = key
  print(
      "API key saved. Change it anytime with: gemcode login\n",
      file=sys.stderr,
  )


def require_google_api_key() -> None:
  if os.environ.get("GOOGLE_API_KEY"):
    return
  raise SystemExit(
      "GOOGLE_API_KEY is not set. Run: gemcode login\n"
      "Or export GOOGLE_API_KEY or add it to a .env file in this directory."
  )


def _initialize_gemcode_project(cfg: GemCodeConfig) -> None:
  """
  Create ``<project>/.gemcode/`` and print a short banner the first time it appears.

  Runs after workspace trust + API key are satisfied so a bare ``gemcode`` REPL
  feels like a guided first-run (Claude Code–style).
  """
  root = cfg.project_root.resolve()
  gem_dir = root / ".gemcode"
  already_there = gem_dir.is_dir()
  try:
    gem_dir.mkdir(parents=True, exist_ok=True)
  except OSError as e:
    print(f"[gemcode] warning: could not create {gem_dir}: {e}", file=sys.stderr)
    return
  if not already_there:
    print(
        "\n── GemCode · Project folder ready ──\n"
        f"  Workspace: {root}\n"
        f"  Config & session data: {gem_dir}/\n"
        "  Optional: add GEMINI.md in the repo root for project context.\n"
        "── Ready. ──\n",
        file=sys.stderr,
    )


async def _run_prompt(
  cfg: GemCodeConfig, prompt: str, session_id: str, *, use_mcp: bool
) -> str:
  load_cli_environment()
  _maybe_prompt_trust(cfg)
  _maybe_prompt_google_api_key()
  require_google_api_key()
  _initialize_gemcode_project(cfg)
  # MCP and OpenAPI toolsets are now loaded inside create_runner() directly.
  runner = create_runner(cfg, extra_tools=None)
  try:
    collected = await run_turn(
        runner,
        user_id="local",
        session_id=session_id,
        prompt=prompt,
        max_llm_calls=cfg.max_llm_calls,
        cfg=cfg,
    )
    return _events_to_text(collected)
  finally:
    # Ensure toolsets with external resources (e.g. Playwright browser) are
    # cleaned up after each CLI invocation.
    await runner.close()


async def _run_repl(cfg: GemCodeConfig, session_id: str, *, use_mcp: bool) -> None:
  """
  Interactive REPL mode (Claude Code-like): keep the session open for multiple turns.
  """
  load_cli_environment()
  _maybe_prompt_trust(cfg)
  _maybe_prompt_google_api_key()
  require_google_api_key()
  _initialize_gemcode_project(cfg)

  # MCP and OpenAPI toolsets are now loaded inside create_runner() directly.
  runner = create_runner(cfg, extra_tools=None)
  try:
    # For CLI UX, show concise tool summaries (helps users see what ran).
    if os.environ.get("GEMCODE_EMIT_TOOL_USE_SUMMARIES") is None:
      os.environ["GEMCODE_EMIT_TOOL_USE_SUMMARIES"] = "1"

    # One-time permission prompt (interactive UX).
    # This maps to the existing flags:
    # - "auto"  => --yes (auto-approve mutating tools)
    # - "ask"   => --interactive-ask (HITL prompts during runs)
    # - "ro"    => read-only (default)
    if os.environ.get("GEMCODE_CLI_PERMISSION_PROMPT", "1").lower() in (
      "1",
      "true",
      "yes",
      "on",
    ):
      try:
        if (
          hasattr(sys.stdin, "isatty")
          and sys.stdin.isatty()
          and not cfg.yes_to_all
          and not cfg.interactive_permission_ask
        ):
          print(
            "Permission mode: [Enter]=read-only, (a)sk each time, (y)es auto-approve (writes + shell)",
            file=sys.stderr,
          )
          choice = input("perm> ").strip().lower()
          if choice in ("y", "yes"):
            cfg.yes_to_all = True
          elif choice in ("a", "ask"):
            cfg.interactive_permission_ask = True
      except EOFError:
        pass

    # Optional terminal UI: single scrollback-style REPL (terminal history, no alt-screen app).
    tui_enabled = os.environ.get("GEMCODE_TUI", "1").lower() in ("1", "true", "yes", "on")
    if tui_enabled:
      term = (os.environ.get("TERM") or "").strip().lower()
      if not sys.stdin.isatty() or not sys.stdout.isatty() or term in ("", "dumb", "unknown"):
        print(
          f"[tui] disabled (stdin/stdout isatty={sys.stdin.isatty()}/{sys.stdout.isatty()}, TERM={term or '<unset>'}); using plain REPL",
          file=sys.stderr,
        )
      else:
        try:
          from gemcode.tui.scrollback import run_gemcode_scrollback_tui

          await run_gemcode_scrollback_tui(
              cfg=cfg,
              runner=runner,
              session_id=session_id,
              extra_tools=None,
          )
          return
        except Exception as e:
          print(
              f"[tui] failed to start: {type(e).__name__}: {e} (falling back to plain REPL).",
              file=sys.stderr,
          )

    print(
      "GemCode CLI is running. Type your prompt and press Enter. (Ctrl+D to exit)",
      file=sys.stderr,
    )
    while True:
      try:
        raw = input("> ")
      except EOFError:
        break

      prompt_text = (raw or "").strip()
      if not prompt_text:
        continue
      if prompt_text in (":q", "quit", "exit", "/exit"):
        break

      slash = await process_repl_slash(
          cfg=cfg,
          runner=runner,
          session_id=session_id,
          prompt_text=prompt_text,
          extra_tools=None,
      )
      if slash is not None:
        if slash.exit_repl:
          break
        if slash.new_session_id is not None:
          session_id = slash.new_session_id
        if slash.skip_model_turn:
          continue
        prompt_text = slash.model_prompt or prompt_text

      apply_capability_routing(cfg, prompt_text, context="prompt")
      cfg.model = pick_effective_model(cfg, prompt_text)
      collected = await run_turn(
        runner,
        user_id="local",
        session_id=session_id,
        prompt=prompt_text,
        max_llm_calls=cfg.max_llm_calls,
        cfg=cfg,
      )
      out = _events_to_text(collected)
      if out:
        print(out)
        print()
  finally:
    await runner.close()


def main() -> None:
  # Reduce startup noise: hide the experimental ReflectAndRetryToolPlugin warning
  # unless explicitly enabled.
  if os.environ.get("GEMCODE_SHOW_EXPERIMENTAL_WARNINGS", "").lower() not in (
    "1",
    "true",
    "yes",
    "on",
  ):
    warnings.filterwarnings(
      "ignore",
      message=r"^\[EXPERIMENTAL\] ReflectAndRetryToolPlugin: .*",
      category=UserWarning,
    )
    # Google SDK warnings are useful for library authors but noisy for CLI users.
    warnings.filterwarnings(
      "ignore",
      message=r"^Interactions usage is experimental.*",
      category=UserWarning,
    )
    warnings.filterwarnings(
      "ignore",
      message=r"^Async interactions client cannot use aiohttp.*",
      category=UserWarning,
    )
    warnings.filterwarnings(
      "ignore",
      message=r"^Warning: there are non-text parts in the response: .*",
      category=UserWarning,
    )
    warnings.filterwarnings(
      "ignore",
      message=r"^\[EXPERIMENTAL\] feature FeatureName\.TOOL_CONFIRMATION.*",
      category=UserWarning,
    )

  # macOS privacy can block Desktop/Documents access for Terminal.app.
  # Provide a clear error if the current directory is not accessible.
  try:
    Path.cwd().resolve()
  except PermissionError:
    raise SystemExit(
      "PermissionError: terminal cannot access this folder. "
      "On macOS: System Settings → Privacy & Security → Files and Folders, "
      "enable Terminal for Desktop Folder (or grant Full Disk Access)."
    )

  # Persist or rotate API key (Claude Code–style `claude login`).
  if len(sys.argv) > 1 and sys.argv[1] == "login":
    load_cli_environment()
    if not (hasattr(sys.stdin, "isatty") and sys.stdin.isatty()):
      raise SystemExit("gemcode login requires an interactive terminal.")
    from gemcode.credentials import credentials_path, save_google_api_key_to_user_store

    print(
        "GemCode login — Google Gemini API key\n"
        "Create one at https://aistudio.google.com/app/apikey\n",
        file=sys.stderr,
    )
    try:
      key = getpass.getpass("API key: ").strip()
    except EOFError:
      raise SystemExit("Aborted.")
    if not key:
      raise SystemExit("Empty key; aborting.")
    save_google_api_key_to_user_store(key)
    os.environ["GOOGLE_API_KEY"] = key
    print(f"Saved to {credentials_path()}", file=sys.stderr)
    return

  # Quick command bypass (no prompt parsing): list available Gemini models.
  if (
    len(sys.argv) > 1
    and sys.argv[1] in ("models", "list-models", "list_models")
  ):
    load_cli_environment()
    _maybe_prompt_google_api_key()
    require_google_api_key()
    from google.genai import Client

    api_key = os.environ["GOOGLE_API_KEY"]

    client = Client(api_key=api_key)
    models = client.models.list()
    show_all = "--show-all" in sys.argv[2:]
    # `models.list()` returns objects; print best-effort fields.
    for m in models:
      name = getattr(m, "name", None)
      actions = getattr(m, "supported_actions", None)
      if not name:
        continue
      if not show_all and actions and isinstance(actions, list):
        # GemCode uses an ADK LlmAgent; it relies on `generateContent`-style models.
        if "generateContent" not in actions:
          continue
      if actions and isinstance(actions, list):
        print(f"{name}\t{','.join(actions)}")
      else:
        print(name)
    return

  # Tool inventory / smoke test.
  if len(sys.argv) > 1 and sys.argv[1] == "tools":
    tools_parser = argparse.ArgumentParser(prog="gemcode tools")
    tools_parser.add_argument(
      "subcommand",
      choices=("list", "smoke"),
      help="Tool inventory operation",
    )
    tools_parser.add_argument(
      "-C",
      "--directory",
      type=Path,
      default=Path.cwd(),
      help="Project root",
    )
    tools_parser.add_argument(
      "--deep-research",
      action="store_true",
      help="Enable deep research built-in tools for inspection",
    )
    tools_parser.add_argument(
      "--maps-grounding",
      action="store_true",
      help="Opt-in to Google Maps grounding during deep-research inspection",
    )
    tools_parser.add_argument(
      "--embeddings",
      action="store_true",
      help="Enable embeddings semantic retrieval tool for inspection",
    )
    tools_parser.add_argument(
      "--memory",
      action="store_true",
      help="Enable persistent memory ingestion tool preload for inspection",
    )
    args = tools_parser.parse_args(sys.argv[2:])

    load_cli_environment()
    cfg = GemCodeConfig(project_root=args.directory)
    cfg.enable_deep_research = bool(args.deep_research)
    cfg.enable_maps_grounding = bool(args.maps_grounding)
    cfg.enable_embeddings = bool(args.embeddings)
    cfg.enable_memory = bool(args.memory)

    inspections = inspect_tools(cfg)
    failures = smoke_tools(inspections)

    if args.subcommand == "list":
      for i in inspections:
        decl = "decl_ok" if i.declaration_present else "no_decl"
        if i.declaration_error:
          decl = "decl_err"
        print(f"{i.name}\t{i.category}\t{i.tool_type}\t{decl}")
        if i.declaration_error:
          print(f"  error: {i.declaration_error}")
      return

    # smoke
    if failures:
      for i in failures:
        print(f"{i.name}\t{ i.category }\tdecl_err")
        if i.declaration_error:
          print(f"  error: {i.declaration_error}")
      raise SystemExit(1)

    print(f"smoke ok: {len(inspections)} tools validated")
    return

  # Live audio mode (Gemini Live API via ADK run_live()).
  if len(sys.argv) > 1 and sys.argv[1] == "live-audio":
    audio_parser = argparse.ArgumentParser(
      prog="gemcode live-audio",
      description="GemCode live audio (mic -> Gemini Live)",
    )
    audio_parser.add_argument(
      "-C",
      "--directory",
      type=Path,
      default=Path.cwd(),
      help="Project root",
    )
    audio_parser.add_argument(
      "--session",
      default=None,
      help="Session id for SQLite-backed history (optional)",
    )
    audio_parser.add_argument(
      "--seconds",
      type=int,
      default=10,
      help="Record mic for N seconds before sending audio",
    )
    audio_parser.add_argument(
      "--rate",
      type=int,
      default=24000,
      help="Input PCM sample rate (Hz)",
    )
    audio_parser.add_argument(
      "--language",
      default=None,
      help="Optional BCP-47 language code (e.g. en-US)",
    )
    audio_parser.add_argument(
      "--yes",
      action="store_true",
      help="Allow write_file / search_replace",
    )
    audio_parser.add_argument(
      "--model",
      default=None,
      help="Override GEMCODE_MODEL (must support AUDIO live streaming)",
    )
    audio_parser.add_argument(
      "--deep-research",
      action="store_true",
      help="Enable deep research tools + routing",
    )
    audio_parser.add_argument(
      "--embeddings",
      action="store_true",
      help="Enable embeddings-based semantic retrieval",
    )

    args = audio_parser.parse_args(sys.argv[2:])
    load_cli_environment()

    cfg = GemCodeConfig(project_root=args.directory)
    cfg.yes_to_all = args.yes
    cfg.enable_deep_research = bool(args.deep_research)
    cfg.enable_embeddings = bool(args.embeddings)
    if args.model:
      cfg.model = args.model
    else:
      cfg.model = cfg.model_audio_live

    _maybe_prompt_trust(cfg)
    _maybe_prompt_google_api_key()
    require_google_api_key()

    session_id = args.session or str(uuid.uuid4())
    from gemcode.live_audio_engine import run_live_audio

    asyncio.run(
      run_live_audio(
        cfg,
        session_id=session_id,
        seconds=args.seconds,
        input_rate=args.rate,
        language_code=args.language,
      )
    )
    print(f"\n[gemcode live-audio] session_id={session_id}", file=sys.stderr)
    return

  # Kairos proactive scheduler daemon.
  if len(sys.argv) > 1 and sys.argv[1] == "kairos":
    kairos_parser = argparse.ArgumentParser(
      prog="gemcode kairos",
      description="Kairos-like proactive scheduler daemon (stdin -> queued jobs).",
    )
    kairos_parser.add_argument(
      "-C",
      "--directory",
      type=Path,
      default=Path.cwd(),
      help="Project root",
    )
    kairos_parser.add_argument(
      "--session",
      default=None,
      help="Session id for SQLite-backed history (optional; defaults to a new uuid).",
    )
    kairos_parser.add_argument(
      "--concurrency",
      type=int,
      default=2,
      help="Max number of concurrent queued jobs.",
    )
    kairos_parser.add_argument(
      "--default-priority",
      type=int,
      default=0,
      help="Priority used for stdin-enqueued jobs.",
    )
    kairos_parser.add_argument(
      "--yes",
      action="store_true",
      help="Allow write_file / search_replace (disables interactive HITL prompts).",
    )
    kairos_parser.add_argument(
      "--interactive-ask",
      action="store_true",
      help="Prompt in-run for mutating tool confirmations (HITL).",
    )
    kairos_parser.add_argument("--model", default=None, help="Override GEMCODE_MODEL")
    kairos_parser.add_argument(
      "--model-mode",
      default=None,
      help="Model mode: auto|fast|balanced|quality (overrides GEMCODE_MODEL_MODE).",
    )
    kairos_parser.add_argument(
      "--deep-research",
      action="store_true",
      help="Enable deep research tools + routing.",
    )
    kairos_parser.add_argument(
      "--maps-grounding",
      action="store_true",
      help="Opt-in to Google Maps grounding tool inside deep-research.",
    )
    kairos_parser.add_argument(
      "--embeddings",
      action="store_true",
      help="Enable embeddings-based semantic retrieval.",
    )
    kairos_parser.add_argument(
      "--capability-mode",
      default=None,
      help="Capability routing: auto|research|embeddings|computer|audio|all (enables tools and routes models).",
    )
    kairos_parser.add_argument(
      "--tool-combination-mode",
      default=None,
      help="Gemini 3 tool context circulation: deep_research|always|never|auto",
    )
    kairos_parser.add_argument(
      "--max-llm-calls",
      type=int,
      default=None,
      metavar="N",
      help="Cap model↔tool iterations for each job message (ADK RunConfig.max_llm_calls).",
    )

    args = kairos_parser.parse_args(sys.argv[2:])
    load_cli_environment()

    cfg = GemCodeConfig(project_root=args.directory)
    if args.model:
      cfg.model_overridden = True
      cfg.model = args.model
      cfg.model_family_mode = "primary"
      if args.model_mode is None:
        cfg.model_mode = "fast"

    cfg.yes_to_all = bool(args.yes)
    if args.interactive_ask:
      cfg.interactive_permission_ask = True
    else:
      if "GEMCODE_INTERACTIVE_PERMISSION_ASK" not in os.environ:
        cfg.interactive_permission_ask = bool(sys.stdin.isatty() and not cfg.yes_to_all)

    cfg.enable_deep_research = bool(args.deep_research)
    cfg.enable_maps_grounding = bool(args.maps_grounding)
    cfg.enable_embeddings = bool(args.embeddings)

    if args.capability_mode is not None:
      cfg.capability_mode = args.capability_mode
    if args.tool_combination_mode is not None:
      cfg.tool_combination_mode = args.tool_combination_mode
    if args.model_mode is not None:
      cfg.model_mode = args.model_mode
    if args.max_llm_calls is not None:
      cfg.max_llm_calls = args.max_llm_calls

    _maybe_prompt_trust(cfg)
    _maybe_prompt_google_api_key()
    require_google_api_key()

    session_id = args.session or str(uuid.uuid4())
    from gemcode.kairos_daemon import KairosDaemon

    daemon = KairosDaemon(
      cfg=cfg,
      concurrency=args.concurrency,
      default_priority=args.default_priority,
    )
    asyncio.run(daemon.run_forever(session_id=session_id))
    print(f"\n[gemcode kairos] session_id={session_id}", file=sys.stderr)
    return

  parser = argparse.ArgumentParser(prog="gemcode", description="Gemini + ADK coding agent")
  parser.add_argument(
    "prompt",
    nargs="?",
    default=None,
    help="Task or question (read from stdin if omitted)",
  )
  parser.add_argument("-C", "--directory", type=Path, default=Path.cwd(), help="Project root")
  parser.add_argument("--session", default=None, help="Session id for SQLite-backed history")
  parser.add_argument("--yes", action="store_true", help="Allow write_file / search_replace")
  parser.add_argument(
    "--interactive-ask",
    action="store_true",
    help="Prompt in-run for mutating tool confirmations (HITL) instead of requiring --yes rerun.",
  )
  parser.add_argument("--model", default=None, help="Override GEMCODE_MODEL")
  parser.add_argument(
      "--model-mode",
      default=None,
      help="Model mode: auto|fast|balanced|quality (overrides GEMCODE_MODEL_MODE)",
  )
  parser.add_argument(
    "--deep-research",
    action="store_true",
    help="Enable deep research tools and route to the deep-research model",
  )
  parser.add_argument(
    "--maps-grounding",
    action="store_true",
    help="Opt-in to Google Maps grounding tool inside deep-research (may be incompatible with other built-in tools depending on model/tooling).",
  )
  parser.add_argument(
    "--embeddings",
    action="store_true",
    help="Enable embeddings-based semantic retrieval (and embedding memory if enabled)",
  )
  parser.add_argument(
    "--capability-mode",
    default=None,
    help="Capability routing: auto|research|embeddings|computer|audio|all (enables tools and routes models)",
  )
  parser.add_argument(
    "--tool-combination-mode",
    default=None,
    help="Gemini 3 tool context circulation: deep_research|always|never|auto",
  )
  parser.add_argument("--mcp", action="store_true", help="Load .gemcode/mcp.json toolsets")
  parser.add_argument(
      "--max-llm-calls",
      type=int,
      default=None,
      metavar="N",
      help="Cap model↔tool iterations for this message (maps to ADK RunConfig.max_llm_calls)",
  )
  args = parser.parse_args()

  load_cli_environment()
  prompt = args.prompt
  interactive_tty = prompt is None and sys.stdin.isatty()

  cfg = GemCodeConfig(project_root=args.directory)
  if args.model:
    cfg.model_overridden = True
  if args.model:
    cfg.model = args.model
    # User explicitly picked a model id, so treat it as primary.
    cfg.model_family_mode = "primary"
    # If the user explicitly sets a model, default to fast mode unless
    # `--model-mode` is also provided.
    if args.model_mode is None:
      cfg.model_mode = "fast"
  cfg.yes_to_all = args.yes
  if args.interactive_ask:
    cfg.interactive_permission_ask = True
  else:
    # If user didn't explicitly set env, default to HITL when we're in a TTY.
    if "GEMCODE_INTERACTIVE_PERMISSION_ASK" not in os.environ:
      cfg.interactive_permission_ask = bool(sys.stdin.isatty() and not cfg.yes_to_all)
  cfg.enable_deep_research = bool(args.deep_research)
  cfg.enable_maps_grounding = bool(args.maps_grounding)
  cfg.enable_embeddings = bool(args.embeddings)
  if args.capability_mode is not None:
    cfg.capability_mode = args.capability_mode
  if args.tool_combination_mode is not None:
    cfg.tool_combination_mode = args.tool_combination_mode
  if args.model_mode is not None:
    cfg.model_mode = args.model_mode
  if args.max_llm_calls is not None:
    cfg.max_llm_calls = args.max_llm_calls

  session_id = args.session or str(uuid.uuid4())

  if interactive_tty:
    asyncio.run(_run_repl(cfg, session_id, use_mcp=args.mcp))
    print(f"\n[gemcode] session_id={session_id}", file=sys.stderr)
    return

  if prompt is None:
    prompt = sys.stdin.read()
  if not prompt.strip():
    parser.error("Empty prompt")

  prompt_text = prompt.strip()
  apply_capability_routing(cfg, prompt_text, context="prompt")
  cfg.model = pick_effective_model(cfg, prompt_text)
  out = asyncio.run(_run_prompt(cfg, prompt_text, session_id, use_mcp=args.mcp))
  if out:
    print(out)
  print(f"\n[gemcode] session_id={session_id}", file=sys.stderr)


if __name__ == "__main__":
  main()
