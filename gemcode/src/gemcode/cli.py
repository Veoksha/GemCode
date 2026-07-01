"""CLI entry: `gemcode "prompt"`."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import re
import sys
import uuid
import warnings
from pathlib import Path

from gemcode.config import GemCodeConfig, apply_super_mode, load_cli_environment
from gemcode.tools_inspector import inspect_tools, smoke_tools
from gemcode.invoke import run_turn
from gemcode.model_routing import pick_effective_model
from gemcode.capability_routing import apply_capability_routing
from gemcode.session_runtime import create_runner
from gemcode.trust import is_trusted_root, trust_root
from gemcode.repl_slash import process_repl_slash
from gemcode.ide_stdio import main as ide_stdio_main
from gemcode.autotune import init_autotune, run_autotune_eval


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
  interactive CLI–style workspace trust prompt.

  On first use in a project root, ask the user to trust the folder so file,
  shell, and git tools can run. If not trusted, we exit before any tool runs.
  """
  if getattr(cfg, "super_mode", False):
    root = cfg.project_root.resolve()
    if not is_trusted_root(root):
      trust_root(root, trusted=True)
    return
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
  One-time interactive prompt for ``GOOGLE_API_KEY`` (like ``gemcode login`` / first-run).

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
  feels like a guided first-run (interactive CLI–style).
  """
  root = cfg.project_root.resolve()
  gem_dir = root / ".gemcode"
  gemcode_md = root / "gemcode.md"
  # Migrate legacy instruction filenames from older installs/tools.
  # Avoid embedding legacy brand strings in the repo by constructing names.
  try:
    legacy = ("c" + "laude.md")
    legacy_upper = legacy[:-3].upper() + legacy[-3:]  # -> "CLAUDE.md"
    legacy_title = legacy[0].upper() + legacy[1:]     # -> "Claude.md"
    for p in (root / legacy, root / legacy_upper, root / legacy_title):
      if not p.is_file():
        continue
      # If gemcode.md doesn't exist, migrate the legacy file into it.
      if not gemcode_md.exists():
        try:
          p.replace(gemcode_md)
          continue
        except Exception:
          try:
            gemcode_md.write_text(
              p.read_text(encoding="utf-8", errors="replace"),
              encoding="utf-8",
            )
            p.unlink(missing_ok=True)  # type: ignore[arg-type]
            continue
          except Exception:
            pass
      # If gemcode.md already exists, remove the legacy file name from the workspace.
      # Preserve content by renaming to a non-legacy filename.
      try:
        dest = root / "gemcode_legacy_instructions.md"
        if not dest.exists():
          p.replace(dest)
        else:
          # If a legacy copy already exists, just delete the legacy-named file.
          p.unlink(missing_ok=True)  # type: ignore[arg-type]
      except Exception:
        pass
  except Exception:
    pass
  already_there = gem_dir.is_dir()
  try:
    gem_dir.mkdir(parents=True, exist_ok=True)
  except OSError as e:
    print(f"[gemcode] warning: could not create {gem_dir}: {e}", file=sys.stderr)
    return
  if not gemcode_md.exists():
    try:
      gemcode_md.write_text(
        "# Project instructions\n\n"
        "- Describe the project purpose here.\n"
        "- Add build, test, and lint commands.\n"
        "- Add architecture notes and conventions GemCode should follow.\n",
        encoding="utf-8",
      )
    except OSError as e:
      print(f"[gemcode] warning: could not create {gemcode_md}: {e}", file=sys.stderr)
  if not already_there:
    print(
        "\n── GemCode · Project folder ready ──\n"
        f"  Workspace: {root}\n"
        f"  Config & session data: {gem_dir}/\n"
        f"  Project instructions: {gemcode_md.name}\n"
        "── Ready. ──\n",
        file=sys.stderr,
    )


async def _run_prompt(
  cfg: GemCodeConfig,
  prompt: str,
  session_id: str,
  *,
  use_mcp: bool,
  attachment_paths: list[Path] | None = None,
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
        attachment_paths=attachment_paths,
    )
    return _events_to_text(collected)
  finally:
    # Ensure toolsets with external resources (e.g. Playwright browser) are
    # cleaned up after each CLI invocation.
    await runner.close()


async def _run_repl(cfg: GemCodeConfig, session_id: str, *, use_mcp: bool) -> None:
  """
  Interactive REPL mode (multi-turn REPL): keep the session open for multiple turns.
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
        if getattr(cfg, "super_mode", False):
          pass
        elif (
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

    # Optional GemCode TUI: scrollback-style REPL (terminal history, no alt-screen app).
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

    try:
      from gemcode.repl_commands import install_readline_slash_completion

      install_readline_slash_completion()
    except Exception:
      pass

    print(
      "GemCode CLI is running. Type your prompt and press Enter. (Ctrl+D to exit)",
      file=sys.stderr,
    )

    def _looks_like_new_skill_request(s: str) -> bool:
      t = (s or "").strip().lower()
      if not t or t.startswith("/"):
        return False
      # Natural language trigger: "make/create/build a new skill/gemskill"
      return bool(
        re.search(r"\b(new|create|make|build)\b", t)
        and re.search(r"\b(gem\s*skill|gemskill|skill)\b", t)
      )

    def _prompt_nonempty(label: str, default: str | None = None) -> str:
      while True:
        try:
          v = input(label).strip()
        except EOFError:
          return default or ""
        if v:
          return v
        if default is not None:
          return default

    def _wizard_create_gemskill() -> str | None:
      """
      Interactive CLI wizard that collects a GemSkill spec, then returns a single
      model prompt instructing the agent to generate the skill folder/files.
      """
      if not (hasattr(sys.stdin, "isatty") and sys.stdin.isatty()):
        return None
      print("\n── GemSkill wizard ──", file=sys.stderr)
      print("We'll create a new skill under `.gemcode/skills/<name>/`.\n", file=sys.stderr)
      name = _prompt_nonempty("skill name (kebab-case, e.g. api-review): ")
      name = (name or "").strip().lower()
      if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
        print("Invalid name. Use lowercase letters/numbers/hyphens (max 64 chars).", file=sys.stderr)
        return None
      desc = _prompt_nonempty("one-line description: ")
      when = _prompt_nonempty("when should it be used (1-2 sentences)? ")
      inputs = _prompt_nonempty("inputs it should accept (bullets or short text): ", default="User request + $ARGUMENTS")
      outputs = _prompt_nonempty("expected output format (short): ", default="Concise checklist + actionable steps")
      tools_pref = _prompt_nonempty(
        "tools: (a)uto, (r)ead-only, (w)eb-research-heavy? [a]: ",
        default="a",
      ).strip().lower()
      use_web = tools_pref.startswith("w")
      read_only = tools_pref.startswith("r")
      examples = _prompt_nonempty("example command(s) user will type (optional): ", default="")

      root = cfg.project_root.resolve()
      skill_dir = root / ".gemcode" / "skills" / name
      skill_md = skill_dir / "SKILL.md"

      prompt_parts: list[str] = [
        "You are creating a **GemCode GemSkill**. Generate a new skill folder and files.\n\n",
        "## Target\n",
        f"- Project root: {root}\n",
        f"- Skill directory: {skill_dir}\n",
        f"- Primary file: {skill_md}\n\n",
        "## Requirements\n",
        f"- Skill name: {name}\n",
        f"- Description: {desc}\n",
        f"- When to use: {when}\n",
        f"- Inputs: {inputs}\n",
        f"- Output expectations: {outputs}\n",
      ]
      if examples:
        prompt_parts.append(f"- Example invocations: {examples}\n")
      prompt_parts.extend(
        [
          "\n",
          "- Write `SKILL.md` with YAML frontmatter using **multiline-friendly** fields when needed.\n",
          "- Include: Purpose, When to use, When NOT to use (guardrails), Inputs, Output format, Workflow, Examples.\n",
          "- Make it **token-efficient**: prefer short checklists and explicit decision gates.\n",
          "- Avoid vague 'ALWAYS trigger' language; provide precise triggers.\n",
          "- If you need templates/checklists, create supporting files in a `references/` subfolder and keep them small.\n",
          "- Do not create vendor-specific instruction files for other assistants.\n\n",
          "## Tooling / research policy\n",
          (
            "- You MAY use web research to find best practices, but only if it materially improves the skill.\n"
            if use_web
            else "- Avoid web research unless strictly necessary.\n"
          ),
          ("- Operate in read-only mode: do not write files.\n" if read_only else "- You are allowed to write the skill files.\n"),
          "\n",
          "## Execution steps\n",
          "1. Create the skill directory if missing.\n",
          "2. Write `SKILL.md` (and any supporting files).\n",
          "3. Validate the YAML frontmatter parses and the skill is usable.\n",
          f"4. Print a short confirmation: created files + how to invoke (e.g. `/skills list`, `/{name} ...`, `/gemskill {name}`).\n",
        ]
      )
      prompt = "".join(prompt_parts)
      return prompt

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

      # Natural language shortcut: "I want to make a new skill" => wizard.
      if _looks_like_new_skill_request(prompt_text):
        wizard_prompt = _wizard_create_gemskill()
        if wizard_prompt:
          prompt_text = wizard_prompt

      cfg.session_skill_expand_session_id = session_id
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
          cfg.session_skill_expand_session_id = session_id
        if slash.skip_model_turn:
          if slash.force_rebuild_runner:
            try:
              _c = runner.close()
              if asyncio.iscoroutine(_c):
                await _c
            except Exception:
              pass
            runner = create_runner(cfg, extra_tools=None)
          continue
        prompt_text = slash.model_prompt or prompt_text

      apply_capability_routing(cfg, prompt_text, context="prompt")
      cfg.model = pick_effective_model(cfg, prompt_text)
      _repl_attach = list(cfg.pending_attachment_paths)
      cfg.pending_attachment_paths.clear()
      collected = await run_turn(
        runner,
        user_id="local",
        session_id=session_id,
        prompt=prompt_text,
        max_llm_calls=cfg.max_llm_calls,
        cfg=cfg,
        attachment_paths=_repl_attach if _repl_attach else None,
      )
      out = _events_to_text(collected)
      if out:
        print(out)
        print()

      try:
        from gemcode.fleet_reports import (
          auto_continue_enabled,
          auto_continue_mode,
          fleet_digest_prompt,
          has_pending_fleet_reports,
          max_auto_chain,
        )

        chain = 0
        while (
          auto_continue_enabled()
          and auto_continue_mode() in ("tui", "both")
          and has_pending_fleet_reports(cfg.project_root)
          and chain < max_auto_chain()
        ):
          chain += 1
          d_prompt = fleet_digest_prompt()
          apply_capability_routing(cfg, d_prompt, context="prompt")
          cfg.model = pick_effective_model(cfg, d_prompt)
          collected2 = await run_turn(
            runner,
            user_id="local",
            session_id=session_id,
            prompt=d_prompt,
            max_llm_calls=cfg.max_llm_calls,
            cfg=cfg,
            attachment_paths=None,
          )
          out2 = _events_to_text(collected2)
          if out2:
            print(out2)
            print("")
      except Exception:
        pass
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

  # Hidden IDE engine mode: `gemcode ide --stdio`
  if len(sys.argv) >= 2 and sys.argv[1] == "ide":
    ide_parser = argparse.ArgumentParser(prog="gemcode ide")
    ide_parser.add_argument(
      "--stdio",
      action="store_true",
      help="Run IDE engine over stdin/stdout (JSONL)",
    )
    ide_args = ide_parser.parse_args(sys.argv[2:])
    if ide_args.stdio:
      ide_stdio_main()
      return
    raise SystemExit("Usage: gemcode ide --stdio")

  # Persist or rotate API key (interactive CLI–style `gemcode login`).
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

  # HTTP API for web UIs and other clients: `gemcode serve`
  if len(sys.argv) > 1 and sys.argv[1] == "serve":
    from gemcode.web.server import main as serve_main

    serve_main(sys.argv[2:])
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

  # Eval harness (AutoResearch-style gates).
  if len(sys.argv) > 1 and sys.argv[1] == "eval":
    eval_parser = argparse.ArgumentParser(prog="gemcode eval")
    eval_parser.add_argument("-C", "--directory", type=Path, default=Path.cwd(), help="Project root")
    eval_parser.add_argument("--llm", action="store_true", help="Include LLM golden prompts (costs tokens)")
    eval_parser.add_argument("--model", default=None, help="Override model for LLM evals")
    args = eval_parser.parse_args(sys.argv[2:])
    from gemcode.evals.harness import run_eval_suite, write_eval_record
    res = run_eval_suite(project_root=args.directory.resolve(), include_llm=bool(args.llm), model=args.model)
    p = write_eval_record(args.directory.resolve(), res)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"\n[gemcode eval] wrote {p}", file=sys.stderr)
    raise SystemExit(0 if res.get("ok") else 1)

  # Autotune scaffolding (AutoResearch-inspired).
  if len(sys.argv) > 1 and sys.argv[1] == "autotune":
    at_parser = argparse.ArgumentParser(prog="gemcode autotune")
    at_parser.add_argument("subcommand", choices=("init", "eval"))
    at_parser.add_argument("-C", "--directory", type=Path, default=Path.cwd(), help="Project root")
    at_parser.add_argument("--tag", default=None, help="Run tag (e.g. apr7)")
    at_parser.add_argument("--llm", action="store_true", help="Include LLM golden prompts (costs tokens)")
    at_parser.add_argument("--model", default=None, help="Override model for LLM evals")
    args = at_parser.parse_args(sys.argv[2:])
    root = args.directory.resolve()
    if args.subcommand == "init":
      if not args.tag:
        raise SystemExit("autotune init requires --tag")
      print(json.dumps(init_autotune(project_root=root, tag=str(args.tag)), ensure_ascii=False, indent=2))
      return
    # eval
    print(json.dumps(run_autotune_eval(project_root=root, include_llm=bool(args.llm), model=args.model), ensure_ascii=False, indent=2))
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
    audio_parser.add_argument(
      "--no-playback",
      action="store_true",
      help="Do not play model audio to speakers (still prints text if any)",
    )
    audio_parser.add_argument(
      "--list-devices",
      action="store_true",
      help="List available audio devices and exit (for mic troubleshooting)",
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

    if args.list_devices:
      try:
        import sounddevice as sd  # type: ignore
      except Exception:
        print(
          "\n[gemcode live-audio] Audio deps missing. Install:\n"
          "  python3 -m pip install -U \"gemcode[live]\"\n",
          file=sys.stderr,
        )
        raise SystemExit(2)
      try:
        devs = sd.query_devices()
        default_in, default_out = sd.default.device
        print("Audio devices:")
        for i, d in enumerate(devs):
          name = str(d.get("name") or "")
          mi = int(d.get("max_input_channels") or 0)
          mo = int(d.get("max_output_channels") or 0)
          mark = ""
          if i == default_in:
            mark += " [default-in]"
          if i == default_out:
            mark += " [default-out]"
          print(f"  {i:>2}: in={mi} out={mo}  {name}{mark}")
        print("\nTip: set GEMCODE_LIVE_AUDIO_INPUT_DEVICE to a device index or name.")
      except Exception as e:
        print(f"[gemcode live-audio] Could not list devices: {e}", file=sys.stderr)
        raise SystemExit(2)
      raise SystemExit(0)

    # One-time explicit permission prompt (HITL) for mic/speaker use.
    if hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
      try:
        ask = os.environ.get("GEMCODE_LIVE_AUDIO_ASK", "1").lower() not in ("0", "false", "no", "off")
        if ask and not args.yes:
          print(
            "\n[gemcode live-audio] Permissions\n"
            "GemCode will access your microphone"
            + (" and play audio to your speakers" if not args.no_playback else "")
            + ".\n"
            "Allow this now? [y/N] ",
            file=sys.stderr,
            end="",
          )
          ans = input().strip().lower()
          if ans not in ("y", "yes"):
            raise SystemExit("live-audio cancelled by user.")
      except EOFError:
        raise SystemExit("live-audio cancelled (no TTY input).")

    try:
      # Suppress non-actionable serialization warning seen in some SDK versions.
      try:
        import warnings as _warnings
        _warnings.filterwarnings(
          "ignore",
          message=r".*Pydantic serializer warnings.*",
          category=UserWarning,
        )
      except Exception:
        pass

      # Some SDK builds print a close-1000 traceback directly to stderr even when it's benign.
      # Capture stderr during the run and suppress that specific known noise.
      _hide = os.environ.get("GEMCODE_LIVE_AUDIO_HIDE_SDK_TRACE", "1").lower() not in (
        "0",
        "false",
        "no",
        "off",
      )
      if _hide:
        import io
        from contextlib import redirect_stderr

        buf = io.StringIO()
        with redirect_stderr(buf):
          asyncio.run(
            run_live_audio(
              cfg,
              session_id=session_id,
              seconds=args.seconds,
              input_rate=args.rate,
              language_code=args.language,
              playback=(not args.no_playback),
            )
          )
        captured = buf.getvalue()
        noisy = (
          "An unexpected error occurred in live flow: 1000" in captured
          or "APIError: 1000" in captured
          or "APIError: 1011" in captured
          or "An unexpected error occurred in live flow: 1011" in captured
        )
        if captured and not noisy:
          # Re-emit unexpected stderr.
          print(captured, file=sys.stderr, end="")
      else:
        asyncio.run(
          run_live_audio(
            cfg,
            session_id=session_id,
            seconds=args.seconds,
            input_rate=args.rate,
            language_code=args.language,
            playback=(not args.no_playback),
          )
        )
    except Exception as e:
      # Some SDK/ADK versions surface a normal websocket close (1000 OK) as an exception.
      try:
        from google.genai.errors import APIError  # type: ignore
        if isinstance(e, APIError) and (getattr(e, "status_code", None) == 1000 or "1000" in str(e)):
          print("\n[gemcode live-audio] Session ended.", file=sys.stderr)
          raise SystemExit(0)
      except Exception:
        pass
      # Gemini Live "1011 Internal error" can surface through different wrappers.
      if "1011" in str(e) or "received 1011" in str(e) or "Internal error encountered" in str(e):
        print(
          "\n[gemcode live-audio] Gemini Live internal error (1011).\n"
          "This is usually transient. Try again, or try:\n"
          "  - set a different live model:  gemcode live-audio --model <id>\n"
          "  - disable playback:           gemcode live-audio --no-playback\n"
          "  - shorten the session:        gemcode live-audio --seconds 10\n",
          file=sys.stderr,
        )
        raise SystemExit(2)
      # websockets can also surface a close directly.
      if "ConnectionClosedOK" in repr(e) or "sent 1000 (OK)" in str(e):
        print("\n[gemcode live-audio] Session ended.", file=sys.stderr)
        raise SystemExit(0)
      raise
    except RuntimeError as e:
      msg = str(e or "")
      if "Mic capture requires `sounddevice` and `numpy`" in msg:
        print(
          "\n[gemcode live-audio] Microphone capture dependencies are missing.\n\n"
          "Install:\n"
          "  python3 -m pip install -U \"gemcode[live]\"\n\n"
          "If that fails on your system, try:\n"
          "  python3 -m pip install -U numpy sounddevice\n\n"
          "Then re-run:\n"
          f"  gemcode live-audio -C {cfg.project_root}\n\n"
          "If the mic is still blocked, enable Microphone access for your terminal app in:\n"
          "  System Settings → Privacy & Security → Microphone\n",
          file=sys.stderr,
        )
        raise SystemExit(2)
      raise
    print(f"\n[gemcode live-audio] session_id={session_id}", file=sys.stderr)
    return

  # Kaira proactive scheduler daemon.
  if len(sys.argv) > 1 and sys.argv[1] in ("kaira", "runtime"):
    is_runtime = sys.argv[1] == "runtime"
    # Optional attach mode: stream runtime events in this terminal.
    if is_runtime and len(sys.argv) > 2 and sys.argv[2] == "attach":
      attach_parser = argparse.ArgumentParser(
        prog="gemcode runtime attach",
        description="Attach to a running GemCode runtime and stream events.",
      )
      attach_parser.add_argument(
        "-C",
        "--directory",
        type=Path,
        default=Path.cwd(),
        help="Project root (used for default socket path).",
      )
      attach_parser.add_argument(
        "--socket",
        default=None,
        help="Override IPC socket path (default: <project>/.gemcode/ipc.sock).",
      )
      attach_args = attach_parser.parse_args(sys.argv[3:])
      load_cli_environment()
      sock = (
        str(attach_args.socket)
        if attach_args.socket
        else str(attach_args.directory.resolve() / ".gemcode" / "ipc.sock")
      )
      async def _attach() -> None:
        from gemcode.kaira_client import KairaIpcClient
        c = await KairaIpcClient.connect(socket_path=sock)
        try:
          await c.subscribe()
          async for msg in c.iter_messages():
            if not isinstance(msg, dict):
              continue
            # Print raw JSONL to keep this universal for logs/tools.
            print(json.dumps(msg, ensure_ascii=False), flush=True)
        finally:
          await c.close()
      asyncio.run(_attach())
      return

    kaira_parser = argparse.ArgumentParser(
      prog=("gemcode runtime" if is_runtime else "gemcode kaira"),
      description=(
        "GemCode runtime daemon (shared always-on brain; stdin -> queued runs)."
        if is_runtime
        else "Background proactive scheduler daemon (stdin -> queued jobs)."
      ),
    )
    kaira_parser.add_argument(
      "-C",
      "--directory",
      type=Path,
      default=Path.cwd(),
      help="Project root",
    )
    kaira_parser.add_argument(
      "--session",
      default=None,
      help="Session id for SQLite-backed history (optional; defaults to a new uuid).",
    )
    kaira_parser.add_argument(
      "--concurrency",
      type=int,
      default=2,
      help="Max number of concurrent queued jobs.",
    )
    kaira_parser.add_argument(
      "--default-priority",
      type=int,
      default=0,
      help="Priority used for stdin-enqueued jobs.",
    )
    kaira_parser.add_argument(
      "--socket",
      default=None,
      help="Override IPC socket path (default: <project>/.gemcode/ipc.sock).",
    )
    kaira_parser.add_argument(
      "--yes",
      action="store_true",
      help="Allow write_file / search_replace (disables interactive HITL prompts).",
    )
    kaira_parser.add_argument(
      "--super",
      action="store_true",
      help="Fully autonomous jobs: auto-approve tools/shell, no HITL (implies --yes).",
    )
    kaira_parser.add_argument(
      "--interactive-ask",
      action="store_true",
      help="Prompt in-run for mutating tool confirmations (HITL).",
    )
    kaira_parser.add_argument("--model", default=None, help="Override GEMCODE_MODEL")
    kaira_parser.add_argument(
      "--model-mode",
      default=None,
      help="Model mode: auto|fast|balanced|quality (overrides GEMCODE_MODEL_MODE).",
    )
    kaira_parser.add_argument(
      "--deep-research",
      action="store_true",
      help="Enable deep research tools + routing.",
    )
    kaira_parser.add_argument(
      "--maps-grounding",
      action="store_true",
      help="Opt-in to Google Maps grounding tool inside deep-research.",
    )
    kaira_parser.add_argument(
      "--embeddings",
      action="store_true",
      help="Enable embeddings-based semantic retrieval.",
    )
    kaira_parser.add_argument(
      "--capability-mode",
      default=None,
      help="Capability routing: auto|research|embeddings|computer|audio|all (enables tools and routes models).",
    )
    kaira_parser.add_argument(
      "--tool-combination-mode",
      default=None,
      help="Gemini 3 tool context circulation: deep_research|always|never|auto",
    )
    kaira_parser.add_argument(
      "--max-llm-calls",
      type=int,
      default=None,
      metavar="N",
      help="Cap model↔tool iterations for each job message (ADK RunConfig.max_llm_calls).",
    )
    kaira_parser.add_argument(
      "--automations",
      action="store_true",
      help="Enable local scheduled automations from .gemcode/automations/*.json.",
    )
    kaira_parser.add_argument(
      "--heartbeat-every-s",
      type=int,
      default=0,
      metavar="N",
      help="Optional heartbeat job interval (seconds). Enqueues heartbeat prompt repeatedly.",
    )
    kaira_parser.add_argument(
      "--heartbeat-prompt",
      default=None,
      help="Prompt text for heartbeat jobs (used with --heartbeat-every-s).",
    )

    args = kaira_parser.parse_args(sys.argv[2:])
    load_cli_environment()

    cfg = GemCodeConfig(project_root=args.directory)
    if getattr(args, "socket", None):
      cfg.runtime_ipc_bind_path = Path(args.socket).expanduser().resolve()
    if args.model:
      cfg.model_overridden = True
      cfg.model = args.model
      cfg.model_family_mode = "primary"
      if args.model_mode is None:
        cfg.model_mode = "fast"

    cfg.yes_to_all = bool(args.yes)
    if getattr(args, "super", False):
      cfg.super_mode = True
    if cfg.super_mode:
      apply_super_mode(cfg)
    elif args.interactive_ask:
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

    # Local automations / heartbeat configuration (implemented in KairaDaemon loop).
    if getattr(args, "automations", False):
      os.environ["GEMCODE_AUTOMATIONS"] = "1"
    hb_every = int(getattr(args, "heartbeat_every_s", 0) or 0)
    if hb_every > 0:
      os.environ["GEMCODE_AUTOMATIONS"] = "1"
      os.environ["GEMCODE_KAIRA_HEARTBEAT_EVERY_S"] = str(hb_every)
      if getattr(args, "heartbeat_prompt", None):
        os.environ["GEMCODE_KAIRA_HEARTBEAT_PROMPT"] = str(args.heartbeat_prompt)

    _maybe_prompt_trust(cfg)
    _maybe_prompt_google_api_key()
    require_google_api_key()

    session_id = args.session or str(uuid.uuid4())
    from gemcode.kaira_daemon import KairaDaemon

    daemon = KairaDaemon(
      cfg=cfg,
      concurrency=args.concurrency,
      default_priority=args.default_priority,
    )
    asyncio.run(daemon.run_forever(session_id=session_id))
    print(
      f"\n[gemcode {'runtime' if is_runtime else 'kaira'}] session_id={session_id}",
      file=sys.stderr,
    )
    return

  parser = argparse.ArgumentParser(prog="gemcode", description="Gemini + ADK coding agent")
  parser.add_argument(
    "prompt",
    nargs="?",
    default=None,
    help="Task or question (read from stdin if omitted)",
  )
  parser.add_argument("-C", "--directory", type=Path, default=Path.cwd(), help="Project root")
  parser.add_argument(
    "--connect",
    default=None,
    metavar="SOCKET",
    help="Connect this REPL/TUI to an existing GemCode runtime (IPC socket path).",
  )
  parser.add_argument("--session", default=None, help="Session id for SQLite-backed history")
  parser.add_argument("--yes", action="store_true", help="Allow write_file / search_replace")
  parser.add_argument(
    "--super",
    action="store_true",
    help="Super mode: auto-approve all tool/shell use, skip HITL and AFC prompts (implies --yes).",
  )
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
  parser.add_argument(
      "--attach",
      "--image",
      dest="attachments",
      action="append",
      default=[],
      metavar="PATH",
      help="Attach file(s) for this message (repeatable): images, PDF, audio, video, text, etc. "
      "(Gemini-supported MIME). Default max ~20 MiB each (GEMCODE_MAX_ATTACHMENT_BYTES). "
      "REPL: /attach or /image <path>.",
  )
  args = parser.parse_args()

  load_cli_environment()
  if getattr(args, "connect", None):
    os.environ["GEMCODE_KAIRA_SOCKET"] = str(args.connect)
    os.environ["GEMCODE_KAIRA_AUTO_CONNECT"] = "1"
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
  if args.super:
    cfg.super_mode = True
  if cfg.super_mode:
    apply_super_mode(cfg)
  elif args.interactive_ask:
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
  _cli_attach = list(args.attachments) if getattr(args, "attachments", None) else []
  out = asyncio.run(
      _run_prompt(
          cfg,
          prompt_text,
          session_id,
          use_mcp=args.mcp,
          attachment_paths=_cli_attach if _cli_attach else None,
      )
  )
  if out:
    print(out)
  print(f"\n[gemcode] session_id={session_id}", file=sys.stderr)


if __name__ == "__main__":
  main()
