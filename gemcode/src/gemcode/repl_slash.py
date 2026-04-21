"""
Shared REPL slash-command dispatcher (CLI plain REPL + scrollback TUI).

Returns ``None`` when the line is not a slash command; otherwise a
`ReplSlashResult` describing how the UI should proceed.
"""

from __future__ import annotations

import os
import shlex
import uuid
from dataclasses import dataclass
from pathlib import Path
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
from gemcode.curated_memory import load_snapshot as _curated_load_snapshot
from gemcode.multimodal_input import resolve_attachment_path
from gemcode.slash_commands import parse_slash_command
from gemcode.skills import discover_skill_metas, expand_skill_text, list_supporting_files, load_skill
from gemcode.output_styles import discover_output_styles, load_output_style
from gemcode.rules import load_rules as _load_rules
from gemcode.session_summariser import summarise_session
from gemcode.trust import is_trusted_root, trust_json_path, trust_root


@dataclass
class ReplSlashResult:
  """How the REPL should handle this input line."""

  exit_repl: bool = False
  new_session_id: str | None = None
  skip_model_turn: bool = False
  model_prompt: str | None = None
  force_rebuild_runner: bool = False  # True when agent config changed (thinking, etc.)


def _clear_session_loaded_skills(cfg: GemCodeConfig) -> None:
  raw = getattr(cfg, "session_loaded_skill_names", None)
  if isinstance(raw, list):
    raw.clear()


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

  # ── /attach (queue files for the next user message: PDF, images, audio, …) ─
  if name in ("attach", "file", "image", "img"):
    raw_i = (sc.args or "").strip()
    if not raw_i or raw_i.lower() in ("help", "?"):
      out("Usage:")
      out("  /attach <path>   Queue a file for the **next** message (repeat for multiple, max 16).")
      out("  /attach list     Show queued paths")
      out("  /attach clear    Clear the queue")
      out("Aliases: /file, /image, /img — same queue.")
      out("Types: Gemini-supported MIME (e.g. images, PDF, audio, video, text). Default max ~20 MiB each.")
      out("Inline prompt: /image <path> <prompt...>  (or use `--` before the prompt)")
      out("CLI:  gemcode -C . --attach ./doc.pdf \"Summarize this\"")
      out()
      return ReplSlashResult(skip_model_turn=True)

    # Parse args as:
    # - list|clear (no further args)
    # - <path> [--] <optional prompt...>
    #
    # We support quoted paths and best-effort unquoted paths with spaces by
    # scanning for the longest prefix that resolves to an existing file.
    try:
      tokens = shlex.split(raw_i, posix=True)
    except ValueError:
      tokens = raw_i.split()

    if not tokens:
      return ReplSlashResult(skip_model_turn=True)

    if len(tokens) == 1 and tokens[0].strip().lower() == "list":
      pend = cfg.pending_attachment_paths
      if not pend:
        out("(no attachments queued)")
      else:
        out("Queued for next message:")
        for i, p in enumerate(pend, 1):
          out(f"  {i}. {p}")
      out()
      return ReplSlashResult(skip_model_turn=True)
    if len(tokens) == 1 and tokens[0].strip().lower() == "clear":
      cfg.pending_attachment_paths.clear()
      out("Attachment queue cleared.")
      out()
      return ReplSlashResult(skip_model_turn=True)

    # Find the longest token prefix that resolves to a real file.
    best_i: int | None = None
    best_resolved: Path | None = None
    for i in range(1, len(tokens) + 1):
      cand = " ".join(tokens[:i]).strip()
      if not cand:
        continue
      resolved_try = resolve_attachment_path(cand, project_root=cfg.project_root)
      if resolved_try.is_file():
        best_i = i
        best_resolved = resolved_try

    # Fallback: treat first token as the path (keeps old behavior).
    if best_i is None or best_resolved is None:
      path_raw = tokens[0]
      resolved = resolve_attachment_path(path_raw, project_root=cfg.project_root)
      remainder_tokens = tokens[1:]
    else:
      path_raw = " ".join(tokens[:best_i]).strip()
      resolved = best_resolved
      remainder_tokens = tokens[best_i:]

    if remainder_tokens and remainder_tokens[0] == "--":
      remainder_tokens = remainder_tokens[1:]
    trailing_prompt = " ".join(remainder_tokens).strip()

    if not resolved.is_file():
      out(f"Not a file: {path_raw}")
      out("(Resolved relative to cwd, then project root.)")
      if trailing_prompt:
        out("Tip: quote paths with spaces, e.g. /image \"./My File.png\" analyze this")
      out()
      return ReplSlashResult(skip_model_turn=True)
    if len(cfg.pending_attachment_paths) >= 16:
      out("Queue full (16 files max). Use /attach clear first.")
      out()
      return ReplSlashResult(skip_model_turn=True)
    cfg.pending_attachment_paths.append(resolved)
    if trailing_prompt:
      out(f"Queued: {resolved} (attaching now)")
      out()
      return ReplSlashResult(model_prompt=trailing_prompt)

    out(f"Queued: {resolved}")
    out(f"  ({len(cfg.pending_attachment_paths)} file(s) — send your next message to attach)")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /skills and /<skill-name> ──────────────────────────────────────────────
  if name in ("skills", "skill"):
    args = (sc.args or "").strip()
    if not args or args.lower() in ("list", "ls", "show"):
      metas = discover_skill_metas(cfg.project_root)
      if not metas:
        out("No GemSkills found.")
        out("Create one at `.gemcode/skills/<name>/SKILL.md` or `~/.gemcode/skills/<name>/SKILL.md`.")
        out()
        return ReplSlashResult(skip_model_turn=True)
      out("GemSkills:")
      for k in sorted(metas.keys()):
        m, _ = metas[k]
        inv = "manual-only" if m.disable_model_invocation else "auto-eligible"
        out(f"  /{m.name} ({inv}) — {m.description}")
      out()
      return ReplSlashResult(skip_model_turn=True)

    parts = args.split()
    sk = parts[0].strip().lower()
    sk_args = " ".join(parts[1:]).strip()
    s = load_skill(cfg.project_root, sk)
    if s is None:
      out(f"Unknown skill: {sk}")
      out("Tip: /skills list")
      out()
      return ReplSlashResult(skip_model_turn=True)
    # Token-efficient one-shot invocation: do NOT inline the full SKILL.md.
    # Instead, point the model at the skill and require it to load/read it as needed.
    files = list_supporting_files(s)
    prompt_parts = [
      f"User invoked GemSkill `/{s.meta.name}`.\n\n",
      f"## Arguments\n{sk_args or '(none)'}\n\n",
      "## Instructions\n",
      "1. Load the skill instructions using the `load_skill` tool (preferred) or by reading the SKILL.md file.\n",
      "2. Only read supporting files if needed (keep it efficient).\n",
      "3. Then carry out the user's request.\n\n",
      f"## Skill file\n{s.skill_md}\n\n",
    ]
    if files:
      prompt_parts.append(f"## Supporting files (optional)\n{', '.join(files)}\n\n")
    prompt_parts.append("Now proceed.")
    prompt = "".join(prompt_parts)
    return ReplSlashResult(model_prompt=prompt)

  # ── /gemskill (load full skill into session system prompt) ────────────────
  if name == "gemskill":
    args_gs = (sc.args or "").strip()
    if not args_gs or args_gs.lower() in ("help", "?"):
      out("Usage:")
      out("  /gemskill <name>   Load an existing GemSkill into this session (system prompt).")
      out("  /gemskill list     List skills you can load")
      out("  /gemskill clear    Unload all session-loaded skills")
      out()
      out("Create a new skill:  /create gemskill <name> [description]")
      out("Edit an existing one: /append gemskill <name> <what to change>")
      out()
      return ReplSlashResult(skip_model_turn=True)
    al = args_gs.lower()
    if al in ("list", "ls", "show"):
      metas_gs = discover_skill_metas(cfg.project_root)
      if not metas_gs:
        out("No GemSkills found.")
        out("Create one: /create gemskill <name> [description]")
        out()
        return ReplSlashResult(skip_model_turn=True)
      out("GemSkills (load with /gemskill <name>):")
      for k in sorted(metas_gs.keys()):
        m, _ = metas_gs[k]
        inv = "manual-only" if m.disable_model_invocation else "auto-eligible"
        out(f"  {m.name} ({inv}) — {m.description}")
      out()
      return ReplSlashResult(skip_model_turn=True)
    if al == "clear":
      _clear_session_loaded_skills(cfg)
      cfg.session_skill_expand_session_id = session_id
      out("Session-loaded GemSkills cleared.")
      out("Runner will rebuild on the next turn.")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    sk_part = args_gs.split()[0].strip().lower()
    s_gs = load_skill(cfg.project_root, sk_part)
    if s_gs is None:
      out(f"Unknown skill: {sk_part}")
      out("Tip: /gemskill list  ·  Create: /create gemskill <name>")
      out()
      return ReplSlashResult(skip_model_turn=True)
    loaded = cfg.session_loaded_skill_names
    if sk_part in loaded:
      out(f"GemSkill `/{sk_part}` is already loaded for this session.")
      out()
      return ReplSlashResult(skip_model_turn=True)
    loaded.append(sk_part)
    cfg.session_skill_expand_session_id = session_id
    out(f"Loaded GemSkill into session: /{sk_part}")
    out("Full skill body is now in the system prompt until /gemskill clear or a new session.")
    out("Runner will rebuild on the next turn.")
    out()
    return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)

  # ── /batch (built-in GemSkill) ─────────────────────────────────────────────
  if name == "batch":
    goal = (sc.args or "").strip()
    if not goal:
      out("Usage: /batch <goal>")
      out("Example: /batch refactor auth module to use new permissions API")
      out()
      return ReplSlashResult(skip_model_turn=True)
    s = load_skill(cfg.project_root, "batch")
    if s is None:
      out("Batch skill unavailable.")
      out()
      return ReplSlashResult(skip_model_turn=True)
    expanded = expand_skill_text(s, arguments=goal, session_id=session_id)
    prompt = (
      "Apply GemSkill `/batch`.\n\n"
      f"## Goal\n{goal}\n\n"
      f"## Skill instructions\n{expanded}\n\n"
      "Now execute the batch workflow end-to-end."
    )
    return ReplSlashResult(model_prompt=prompt)

  # ── /create gemskill ──────────────────────────────────────────────────────
  if name == "create":
    args = (sc.args or "").strip()
    if not args:
      out("Usage: /create gemskill <name> [description]")
      out()
      return ReplSlashResult(skip_model_turn=True)
    parts = args.split()
    sub = parts[0].lower()
    if sub != "gemskill":
      out(f"Unknown /create subcommand: {sub}")
      out("Usage: /create gemskill <name> [description]")
      out()
      return ReplSlashResult(skip_model_turn=True)
    if len(parts) < 2:
      out("Usage: /create gemskill <name> [description]")
      out("Example: /create gemskill explain-code Explains code with diagrams and analogies")
      out()
      return ReplSlashResult(skip_model_turn=True)

    skill_name = parts[1].strip().lower()
    desc = " ".join(parts[2:]).strip() or f"Describe what /{skill_name} does and when to use it."

    import re
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", skill_name):
      out("Invalid skill name. Use lowercase letters, numbers, hyphens only (max 64 chars).")
      out("Example: explain-code, test-code, deploy-prod")
      out()
      return ReplSlashResult(skip_model_turn=True)

    skill_dir = cfg.project_root / ".gemcode" / "skills" / skill_name
    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists():
      out(f"Skill already exists: {skill_md}")
      out("Tip: edit the SKILL.md file to customize it.")
      out()
      return ReplSlashResult(skip_model_turn=True)

    try:
      skill_dir.mkdir(parents=True, exist_ok=True)
      template = (
        "---\n"
        f"name: {skill_name}\n"
        f"description: {desc}\n"
        "disable-model-invocation: false\n"
        "---\n\n"
        f"# GemSkill: /{skill_name}\n\n"
        "## Purpose\n"
        f"{desc}\n\n"
        "## When to use\n"
        "- Use this skill when the user request matches the Purpose above.\n"
        "- If the request does not match, do not force the workflow—switch back to normal behavior.\n\n"
        "## Inputs\n"
        "- **Arguments**: `$ARGUMENTS` (all args) and `$0`, `$1`, ... (positional).\n"
        "- **Project context**: use repo tools to read the real codebase instead of guessing.\n\n"
        "## Output expectations\n"
        "- Produce an answer that is complete, accurate, and minimal.\n"
        "- If editing code: provide a tight change set and a clear test plan.\n\n"
        "## Workflow (world-class default)\n"
        "1. Clarify the goal from the user's request (1 sentence).\n"
        "2. Gather evidence with read/search tools before proposing changes.\n"
        "3. Choose an approach and state constraints/trade-offs briefly.\n"
        "4. Execute the smallest correct set of steps.\n"
        "5. Verify (tests/lints/smoke) when applicable.\n"
        "6. Summarize results and list any follow-ups.\n\n"
        "## Guardrails\n"
        "- Never invent APIs, files, or commands—verify with tools.\n"
        "- Avoid large rewrites unless explicitly requested.\n"
        "- Do not create vendor-specific files like `CLAUDE.md` or `AGENTS.md`.\n"
        "- Don’t leak secrets; refuse if the user asks for credentials.\n\n"
        "## Tooling guidance\n"
        "- Prefer `read_file`/`grep_content`/`glob_files`/`repo_map` for discovery.\n"
        "- Prefer `search_replace` for small edits; `write_file` only for new files.\n"
        "- Use `bash`/`run_command` for tests/builds only when allowed by permissions.\n\n"
        "## Examples\n"
        "### Example 1\n"
        f"User: `/{skill_name} $ARGUMENTS`\n"
        "Assistant: (apply this workflow; show evidence; then execute)\n\n"
        "### Example 2\n"
        f"User: `/{skill_name}`\n"
        "Assistant: (if missing arguments, proceed with safe defaults or ask a single clarifying question)\n\n"
        "## Supporting files (optional)\n"
        "- Put templates, checklists, or scripts next to this SKILL.md and reference them using `${GEMCODE_SKILL_DIR}`.\n"
      )
      skill_md.write_text(template, encoding="utf-8")
    except OSError as e:
      out(f"Error creating skill: {e}")
      out()
      return ReplSlashResult(skip_model_turn=True)

    out(f"Created GemSkill: /{skill_name}")
    out(f"Path: {skill_md}")
    out("Try: /gemskill " + skill_name + "   or   /" + skill_name + " <args> for a one-shot turn")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /append gemskill (iterate existing SKILL.md) ───────────────────────────
  if name == "append":
    raw_ap = (sc.args or "").strip()
    parts_ap = raw_ap.split(None, 2)
    if len(parts_ap) < 3 or parts_ap[0].lower() != "gemskill":
      out("Usage: /append gemskill <name> <what to add or change>")
      out("Example: /append gemskill review-code Add a checklist for API security")
      out()
      return ReplSlashResult(skip_model_turn=True)
    sk_ap = parts_ap[1].strip().lower()
    instruction_ap = parts_ap[2].strip()
    s_ap = load_skill(cfg.project_root, sk_ap)
    if s_ap is None:
      out(f"Unknown skill: {sk_ap}")
      out("Tip: /gemskill list")
      out()
      return ReplSlashResult(skip_model_turn=True)
    files_ap = list_supporting_files(s_ap)
    prompt_ap = (
        f"The user wants to **iterate** on an existing GemSkill ({sk_ap!r}).\n\n"
        f"## Skill file (primary edit target)\n`{s_ap.skill_md}`\n\n"
        f"## User request\n{instruction_ap}\n\n"
        "## Instructions\n"
        "1. Read the full SKILL.md (and supporting files only if needed).\n"
        "2. Apply the user's request: clarify steps, add sections, improve examples, fix mistakes.\n"
        "3. Preserve valid YAML frontmatter (`name`, `description`, etc.) unless the user asked to rename.\n"
        "4. Save changes with `search_replace` or `write_file`.\n"
        "5. Summarize what you changed.\n\n"
        + (f"## Supporting files (optional)\n{', '.join(files_ap)}\n\n" if files_ap else "")
    )
    return ReplSlashResult(model_prompt=prompt_ap)

  # ── /style ────────────────────────────────────────────────────────────────
  if name == "style":
    args = (sc.args or "").strip()
    styles = discover_output_styles(cfg.project_root)
    if not args or args.lower() in ("list", "ls", "show"):
      active = getattr(cfg, "output_style", None)
      out(f"output_style: {active if active else '(none)'}")
      if not styles:
        out("No output styles found.")
        out("Create one at `.gemcode/output-styles/<name>.md` or `~/.gemcode/output-styles/<name>.md`.")
        out()
        return ReplSlashResult(skip_model_turn=True)
      out("Available styles:")
      for k in sorted(styles.keys()):
        out(f"  {k}\t({styles[k]})")
      out()
      return ReplSlashResult(skip_model_turn=True)

    choice = args.strip().lower()
    if choice in ("off", "none", "clear", "reset", "default"):
      setattr(cfg, "output_style", None)
      out("output_style: (none)")
      out("Runner will rebuild on next turn to apply changes.")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)

    if choice not in styles:
      out(f"Unknown style: {choice}")
      out("Tip: /style to list")
      out()
      return ReplSlashResult(skip_model_turn=True)

    # Validate it loads.
    if load_output_style(cfg.project_root, choice) is None:
      out(f"Could not load style: {choice}")
      out()
      return ReplSlashResult(skip_model_turn=True)

    setattr(cfg, "output_style", choice)
    out(f"output_style: {choice}")
    out("Runner will rebuild on next turn to apply changes.")
    out()
    return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)

  # ── /caveman (shortcut to built-in output styles) ─────────────────────────
  if name == "caveman":
    args = (sc.args or "").strip().lower()
    # Levels map to built-in output styles (still overridable by project/user styles).
    mapping = {
      "": "caveman",
      "full": "caveman",
      "lite": "caveman-lite",
      "ultra": "caveman-ultra",
      "wenyan": "caveman-wenyan",
      "wenyan-full": "caveman-wenyan",
      "wenyan-lite": "caveman-wenyan-lite",
      "wenyan-ultra": "caveman-wenyan-ultra",
    }
    if args in ("off", "stop", "normal", "none", "clear", "reset", "default"):
      setattr(cfg, "output_style", None)
      out("caveman: off (output_style cleared)")
      out("Runner will rebuild on next turn to apply changes.")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    if args not in mapping:
      out("Usage:")
      out("  /caveman                (full)")
      out("  /caveman lite|full|ultra")
      out("  /caveman wenyan-lite|wenyan|wenyan-ultra")
      out("  /caveman off")
      out()
      return ReplSlashResult(skip_model_turn=True)

    choice = mapping[args]
    styles = discover_output_styles(cfg.project_root)
    if choice not in styles or load_output_style(cfg.project_root, choice) is None:
      out(f"caveman: style unavailable: {choice}")
      out("Tip: update GemCode, or create a custom style at .gemcode/output-styles/")
      out()
      return ReplSlashResult(skip_model_turn=True)

    setattr(cfg, "output_style", choice)
    out(f"caveman: on — output_style: {choice}")
    out("Runner will rebuild on next turn to apply changes.")
    out()
    return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)

  # ── /caveman:compress (alias for /compress-memory) ────────────────────────
  if name in ("caveman:compress", "caveman-compress", "caveman:compress-memory"):
    args = (sc.args or "").strip()
    parts = args.split()
    if not parts:
      out("Usage:")
      out("  /caveman:compress <path> [lite|full|ultra]")
      out("Note: mode defaults based on active /caveman level (or full).")
      out()
      return ReplSlashResult(skip_model_turn=True)

    target = parts[0]
    mode = (parts[1].strip().lower() if len(parts) >= 2 else "")
    if mode and mode not in ("lite", "full", "ultra"):
      out(f"Unknown mode: {mode}")
      out("Use: lite|full|ultra (or omit to auto-pick from current caveman level)")
      out()
      return ReplSlashResult(skip_model_turn=True)

    # Auto-pick mode from current output_style if not provided.
    if not mode:
      os_ = (getattr(cfg, "output_style", None) or "").lower()
      if os_ in ("caveman-lite",):
        mode = "lite"
      elif os_ in ("caveman-ultra",):
        mode = "ultra"
      else:
        mode = "full"

    # Dispatch as a model turn so the agent runs the tool and reports results.
    prompt = (
      "Compress a memory file now.\n\n"
      f"- target: `{target}`\n"
      f"- mode: `{mode}`\n\n"
      "Call `compress_memory_file(path=..., mode=...)` and report:\n"
      "- ok/error\n"
      "- path + backup_path\n"
      "- warnings (if any)\n"
      "- chars_before/chars_after\n"
    )
    return ReplSlashResult(model_prompt=prompt)

  # ── /rules ────────────────────────────────────────────────────────────────
  if name == "rules":
    rules = _load_rules(cfg.project_root, touched_paths=None)
    if not rules:
      out("No rules loaded.")
      out("Create rules under `.gemcode/rules/*.md` (optional frontmatter: `paths:`).")
      out()
      return ReplSlashResult(skip_model_turn=True)
    out("Loaded rules:")
    for r in rules:
      gate = f" paths={r.paths}" if r.paths else ""
      out(f"  - {r.name}\t({r.path}){gate}")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /trust (workspace permission for tools) ─────────────────────────────────
  if name == "trust":
    args_s = (sc.args or "").strip().lower()
    root = cfg.project_root.resolve()
    tpath = trust_json_path()

    if args_s in ("", "status", "show"):
      if is_trusted_root(root):
        out(f"Workspace is trusted:\n  {root}")
      else:
        out(f"Workspace is NOT trusted:\n  {root}")
        out("File, shell, and git tools require trust. Use: /trust on")
      out(f"Trust database: {tpath}")
      out()
      return ReplSlashResult(skip_model_turn=True)

    if args_s in ("on", "yes", "y", "1", "true", "enable"):
      trust_root(root, trusted=True)
      out(f"Trusted:\n  {root}")
      out(f"Saved to {tpath}")
      out()
      return ReplSlashResult(skip_model_turn=True)

    if args_s in ("off", "no", "n", "0", "false", "disable", "revoke"):
      trust_root(root, trusted=False)
      out(f"Removed trust for:\n  {root}")
      out("Tools will refuse until you run /trust on (or approve on next CLI start).")
      out()
      return ReplSlashResult(skip_model_turn=True)

    out("Usage:")
    out("  /trust           Show whether this project root is trusted")
    out("  /trust on        Trust this workspace (required for file/shell/git tools)")
    out("  /trust off       Stop trusting this workspace")
    out(f"  (stored under {tpath.parent}/)")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /super (fully autonomous: no HITL, auto-approve tools) ─────────────────
  if name == "super":
    from gemcode.config import apply_super_mode

    args_s = (sc.args or "").strip().lower()
    if args_s in ("off", "0", "false", "no"):
      cfg.super_mode = False
      out("Super mode: off (yes_to_all unchanged; restart or adjust flags to change approvals).")
      out()
      return ReplSlashResult(skip_model_turn=True)
    cfg.super_mode = True
    apply_super_mode(cfg)
    out("Super mode: on — mutating/shell tools and ADK confirmations auto-approved; no AFC stdin prompt.")
    out("Equivalent: gemcode --super  or  GEMCODE_SUPER_MODE=1")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /add-dir (safe multi-root access) ──────────────────────────────────────
  if name in ("add-dir", "add_dir", "adddir"):
    args = (sc.args or "").strip()
    added: dict[str, Path] = getattr(cfg, "_added_dirs", None) or {}
    setattr(cfg, "_added_dirs", added)

    if not args or args.lower() in ("list", "ls", "show"):
      if not added:
        out("No added directories.")
        out("Add one: /add-dir /path/to/dir")
        out()
        return ReplSlashResult(skip_model_turn=True)
      out("Added directories (read/search only):")
      for name2, p2 in sorted(added.items()):
        out(f"  {name2}\t{p2}")
      out()
      out("Use paths as: <name>/<relative-path>")
      out("Example: read_file(\"name/README.md\")")
      out()
      return ReplSlashResult(skip_model_turn=True)

    parts = args.split()
    if parts[0].lower() in ("remove", "rm", "del") and len(parts) >= 2:
      key = parts[1].strip()
      if key in added:
        removed = added.pop(key)
        out(f"Removed: {key} ({removed})")
      else:
        out(f"Not found: {key}")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)

    # Add path
    raw = args
    p = Path(os.path.expanduser(raw)).resolve()
    if not p.is_dir():
      out(f"Not a directory: {p}")
      out()
      return ReplSlashResult(skip_model_turn=True)

    # Name = basename, de-dupe by suffix.
    base = p.name or "dir"
    name2 = base
    if name2 in added and added[name2] != p:
      i = 2
      while f"{base}{i}" in added and added[f"{base}{i}"] != p:
        i += 1
      name2 = f"{base}{i}"
    added[name2] = p

    out(f"Added directory: {name2}")
    out(f"Path: {p}")
    out("Use it as: " + f"{name2}/<path>")
    out("Note: config (rules/styles/settings) is NOT loaded from added dirs.")
    out()
    return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)

  # ── /diff ─────────────────────────────────────────────────────────────────
  if name == "diff":
    import subprocess
    import shutil
    import difflib

    args = (sc.args or "").strip()
    root = cfg.project_root.resolve()

    def _checkpoint_diff_text(checkpoint_id: str, *, max_chars: int = 60_000) -> str | None:
      """
      Unified diff of checkpoint snapshot -> current workspace.
      """
      from gemcode.checkpoints import list_checkpoints
      cps = list_checkpoints(root, limit=200)
      man = next((c for c in cps if (c.get("id") or "") == checkpoint_id), None)
      if not man:
        return None
      base = root / ".gemcode" / "checkpoints" / checkpoint_id
      files_dir = base / "files"
      out_lines: list[str] = []
      for f in (man.get("files") or []):
        rel = str(f.get("path") or "")
        if not rel:
          continue
        existed = bool(f.get("existed"))
        cur_path = (root / rel)
        old_bytes = b""
        if existed:
          snap = files_dir / rel
          if snap.is_file():
            try:
              old_bytes = snap.read_bytes()
            except Exception:
              old_bytes = b""
        old_txt = old_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)
        try:
          cur_txt = cur_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if cur_path.is_file() else []
        except Exception:
          cur_txt = []

        # If file didn't exist at checkpoint but exists now: treat as add.
        # If existed at checkpoint but missing now: treat as delete.
        fromfile = f"{checkpoint_id}:{rel}"
        tofile = f"WORKSPACE:{rel}"
        diff = difflib.unified_diff(old_txt, cur_txt, fromfile=fromfile, tofile=tofile, n=3)
        chunk = "".join(diff)
        if chunk.strip():
          out_lines.append(chunk)
        if sum(len(x) for x in out_lines) > max_chars:
          out_lines.append("\n… [truncated]\n")
          break
      return "".join(out_lines).strip() if out_lines else "(no changes compared to checkpoint)"

    git = shutil.which("git")
    if git and (root / ".git").exists() and not args.startswith("cp_") and args.lower() not in ("last", "checkpoint"):
      # Git diff viewer (text).
      cmd = [git, "--no-pager", "diff"]
      if args:
        # allow: /diff --cached, /diff HEAD~1, /diff --stat, etc.
        cmd = [git, "--no-pager", "diff", *args.split()]
      try:
        p = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=15)
        out_txt = (p.stdout or "").strip()
        if not out_txt:
          out("(no git diff)")
          out()
          return ReplSlashResult(skip_model_turn=True)
        # Avoid dumping enormous diffs.
        if len(out_txt) > 40_000:
          out(out_txt[:40_000])
          out("\n… [truncated]  Tip: /diff --stat or narrow the diff.")
        else:
          out(out_txt)
        out()
        return ReplSlashResult(skip_model_turn=True)
      except Exception as e:
        out(f"[gemcode] git diff failed: {e}")
        out()
        return ReplSlashResult(skip_model_turn=True)

    # Checkpoint diff mode:
    # - /diff cp_<id>
    # - /diff last
    # - /diff checkpoint <id>
    try:
      from gemcode.checkpoints import list_checkpoints
      cps = list_checkpoints(root, limit=50)
    except Exception:
      cps = []

    want = args.strip()
    cp_id: str | None = None
    if want.lower() == "last" or want == "":
      cp_id = (cps[0].get("id") if cps else None)
    elif want.lower().startswith("checkpoint "):
      cp_id = want.split(None, 1)[1].strip() if " " in want else None
    elif want.startswith("cp_"):
      cp_id = want.split()[0].strip()

    if cp_id:
      txt = _checkpoint_diff_text(cp_id)
      if txt is None:
        out(f"Unknown checkpoint: {cp_id}")
        out("Tip: /rewind list")
        out()
        return ReplSlashResult(skip_model_turn=True)
      if len(txt) > 60_000:
        out(txt[:60_000])
        out("\n… [truncated]")
      else:
        out(txt)
      out()
      return ReplSlashResult(skip_model_turn=True)

    # Fallback: show recent checkpoints and how to diff.
    try:
      from gemcode.checkpoints import list_checkpoints
      cps = list_checkpoints(root, limit=5)
    except Exception:
      cps = []
    if not cps:
      out("No git repo and no checkpoints found to diff.")
      out()
      return ReplSlashResult(skip_model_turn=True)
    out("No git repo detected. Recent checkpoints:")
    for c in cps:
      cid = c.get("id")
      op = c.get("op")
      files = c.get("files") or []
      out(f"  {cid}\t{op}\tfiles={len(files)}")
    out("Tip: /diff last  or  /diff <checkpoint_id>  (e.g. /diff cp_123...)")
    out("Tip: /rewind <checkpoint_id> to restore.")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /rewind (checkpoint restore) ──────────────────────────────────────────
  if name in ("rewind", "checkpoint"):
    args = (sc.args or "").strip()
    from gemcode.checkpoints import list_checkpoints, undo_checkpoint

    if not args or args.lower() in ("list", "ls", "show"):
      cps = list_checkpoints(cfg.project_root, limit=20)
      if not cps:
        out("No checkpoints found.")
        out()
        return ReplSlashResult(skip_model_turn=True)
      out("Checkpoints (newest first):")
      out("─" * 70)
      for c in cps:
        cid = c.get("id") or ""
        op = c.get("op") or ""
        ts = c.get("ts_ms") or 0
        files = c.get("files") or []
        out(f"  {cid}\t{op}\tfiles={len(files)}\tts_ms={ts}")
      out()
      out("Restore: /rewind <checkpoint_id>")
      out("Undo latest: /rewind")
      out()
      return ReplSlashResult(skip_model_turn=True)

    # Restore a specific checkpoint id
    cp_id = args.split()[0].strip()
    res = undo_checkpoint(cfg.project_root, checkpoint_id=cp_id)
    if res.get("error"):
      out(f"rewind error: {res.get('error')}")
      out()
      return ReplSlashResult(skip_model_turn=True)
    restored = res.get("restored") or []
    out(f"Rewound to checkpoint: {res.get('checkpoint_id')}")
    if restored:
      out("Restored paths:")
      for p in restored[:60]:
        out(f"  - {p}")
      if len(restored) > 60:
        out(f"  … (+{len(restored) - 60} more)")
    else:
      out("(No files changed by this rewind.)")
    out()
    return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)

  if name == "doctor":
    out("\n".join(format_doctor_lines(cfg)))
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /eval ─────────────────────────────────────────────────────────────────
  if name == "eval":
    raw_args = (sc.args or "").strip().lower()
    include_llm = "llm" in raw_args or "--llm" in raw_args
    from gemcode.evals.harness import run_eval_suite, write_eval_record

    try:
      res = run_eval_suite(
        project_root=cfg.project_root,
        include_llm=include_llm,
        model=None,
        session_cfg=cfg,
        extra_tools=extra_tools,
      )
    except Exception as e:
      out(f"eval failed: {type(e).__name__}: {e}")
      out()
      return ReplSlashResult(skip_model_turn=True)
    try:
      rec_path = write_eval_record(cfg.project_root, res)
      out(f"eval: ok={res.get('ok')}  score={float(res.get('score', 0)):.2f}  elapsed_s={float(res.get('elapsed_s', 0)):.1f}  → {rec_path}")
    except OSError:
      out(f"eval: ok={res.get('ok')}  score={float(res.get('score', 0)):.2f}  elapsed_s={float(res.get('elapsed_s', 0)):.1f}")
    for row in res.get("results") or []:
      nm = row.get("name", "?")
      ok = row.get("ok", False)
      out(f"  {nm}: {'ok' if ok else 'FAIL'}")
      if not ok and row.get("details"):
        det = str(row["details"]).strip()
        if det:
          snippet = det[-600:] if len(det) > 600 else det
          out(f"    {snippet}")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /autotune ───────────────────────────────────────────────────────────────
  if name == "autotune":
    parts = (sc.args or "").strip().split()
    if not parts:
      out("Usage:")
      out("  /autotune init <tag>   Create git branch autotune/<tag> (requires git repo)")
      out("  /autotune eval [llm]   Run eval suite and append .gemcode/evals/autotune_ledger.jsonl")
      out()
      return ReplSlashResult(skip_model_turn=True)
    sub = parts[0].lower()
    if sub == "init":
      if len(parts) < 2:
        out("Usage: /autotune init <tag>")
        out()
        return ReplSlashResult(skip_model_turn=True)
      tag = parts[1].strip()
      from gemcode.autotune import init_autotune

      r = init_autotune(project_root=cfg.project_root, tag=tag)
      if r.get("error"):
        out(f"autotune init: {r.get('error')}")
        if r.get("output"):
          out(str(r["output"])[-800:])
      else:
        out(f"autotune init: {r.get('status')}  branch={r.get('branch')}")
      out()
      return ReplSlashResult(skip_model_turn=True)
    if sub == "eval":
      include_llm = any(p.lower() in ("llm", "--llm") for p in parts[1:])
      from gemcode.autotune import run_autotune_eval

      try:
        r = run_autotune_eval(
          project_root=cfg.project_root,
          include_llm=include_llm,
          model=None,
          session_cfg=cfg,
          extra_tools=extra_tools,
        )
      except Exception as e:
        out(f"autotune eval failed: {type(e).__name__}: {e}")
        out()
        return ReplSlashResult(skip_model_turn=True)
      out(f"autotune eval: ok={r.get('ok')}  score={float(r.get('score', 0)):.2f}")
      if r.get("record_path"):
        out(f"  record: {r['record_path']}")
      if r.get("ledger_path"):
        out(f"  ledger: {r['ledger_path']}")
      out()
      return ReplSlashResult(skip_model_turn=True)
    out(f"Unknown /autotune subcommand: {sub}")
    out("Usage: /autotune init <tag>  ·  /autotune eval [llm]")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /curated (curated memory files) ────────────────────────────────────────
  if name in ("curated", "memory-files", "memoryfiles"):
    snap = _curated_load_snapshot(cfg.project_root, max_chars=8000)
    out("Curated memory (injected when memory is on):")
    out(f"  project: {snap.get('memory_path')}")
    out(f"  user:    {snap.get('user_path')}")
    out(f"  loaded:  {snap.get('chars', 0)} chars  exists={snap.get('exists')}")
    out()
    txt = (snap.get("text") or "").strip()
    if txt:
      out("--- snapshot ---")
      out(txt)
      out("--- end ---")
    else:
      out("(empty — create .gemcode/GEMCODE_MEMORY.md and GEMCODE_USER.md)")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /login ─────────────────────────────────────────────────────────────────
  if name == "login":
    from gemcode.credentials import credentials_path

    out("API keys are not stored inside the REPL. Use a separate terminal:")
    out()
    out("  gemcode login")
    out()
    out("Creates or updates your key at:")
    out(f"  {credentials_path()}")
    out()
    out("Get a key: https://aistudio.google.com/app/apikey")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /live-audio ────────────────────────────────────────────────────────────
  if name in ("live-audio", "liveaudio"):
    out("Live audio (microphone → Gemini Live) runs as a dedicated CLI, not inside this REPL.")
    out()
    out("Example:")
    out(f"  gemcode live-audio -C {cfg.project_root}")
    out()
    out("Flags: --seconds N  --rate 24000  --language en-US  --model <id>")
    out("       --yes  --deep-research  --embeddings  --session <uuid>")
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
    gemcode_md = cfg.project_root / "gemcode.md"
    if gemcode_md.exists() and (sc.args or "").strip().lower() not in ("force", "overwrite", "-f"):
      out(f"gemcode.md already exists at {gemcode_md}.")
      out("Use /init force to regenerate it, or edit it manually.")
      out()
      return ReplSlashResult(skip_model_turn=True)
    # Dispatch to the model to analyze the project and write gemcode.md.
    init_prompt = (
      "Analyze this codebase and generate a gemcode.md file for me.\n\n"
      "To do this:\n"
      "1. Run `list_directory('.')` to understand the project structure\n"
      "2. Read `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `README.md` "
      "or equivalent to understand the project type and dependencies\n"
      "3. Look at the source directory structure (src/, lib/, app/, etc.)\n"
      "4. Check for test directories and test runner config\n"
      "5. Look for linting/formatting config files (.eslintrc, .prettierrc, ruff.toml, etc.)\n\n"
      "Write **only** to `gemcode.md` at the project root. Do **not** create "
      "`CLAUDE.md`, `AGENTS.md`, `.cursorrules`, or similar.\n\n"
      "Then write a gemcode.md file at the project root containing:\n"
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
      "Keep it under 200 lines. Write the file to gemcode.md now."
    )
    out("Analyzing project to generate gemcode.md…")
    out("(GemCode will read the project structure and write a starting gemcode.md)")
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

  # ── /mcp (Model Context Protocol toolsets) ────────────────────────────────
  if name == "mcp":
    args_m = (sc.args or "").strip()
    sub = (args_m.split()[0].strip().lower() if args_m else "status")
    mcp_path = cfg.project_root / ".gemcode" / "mcp.json"

    if sub in ("help", "?"):
      out("Usage:")
      out("  /mcp                 Show MCP config + loaded toolsets (same as /mcp status)")
      out("  /mcp status          Show MCP config + loaded toolsets")
      out("  /mcp list            List configured servers from .gemcode/mcp.json")
      out("  /mcp reload          Rebuild runner to reload MCP toolsets from disk")
      out()
      out("Config:")
      out(f"  {mcp_path}")
      out()
      return ReplSlashResult(skip_model_turn=True)

    if sub in ("reload", "refresh"):
      out("MCP: runner will rebuild on the next turn (reload .gemcode/mcp.json).")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)

    # Read config if present.
    servers: list[dict] = []
    parse_error: str | None = None
    if mcp_path.is_file():
      try:
        import json

        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        servers = list(data.get("servers") or [])
      except Exception as e:
        parse_error = str(e)

    # Inspect currently loaded toolsets (best-effort; depends on how caller wired extra_tools).
    loaded_prefixes: list[str] = []
    loaded_count = 0
    try:
      from google.adk.tools.mcp_tool.mcp_toolset import McpToolset  # type: ignore

      for t in list(extra_tools or []):
        if isinstance(t, McpToolset):
          loaded_count += 1
          try:
            p = getattr(t, "tool_name_prefix", None)
            if isinstance(p, str) and p and p not in loaded_prefixes:
              loaded_prefixes.append(p)
          except Exception:
            pass
    except Exception:
      # MCP extras not installed or ADK missing MCP toolset types.
      pass

    if sub in ("list", "ls"):
      out(f"mcp.json: {mcp_path} ({'exists' if mcp_path.is_file() else 'missing'})")
      if parse_error:
        out(f"error: {parse_error}")
        out()
        return ReplSlashResult(skip_model_turn=True)
      if not servers:
        out("(no servers configured)")
        out()
        return ReplSlashResult(skip_model_turn=True)
      out("Servers:")
      for s in servers[:200]:
        try:
          nm = (s.get("name") or "mcp").strip()
          kind = "stdio" if "stdio" in s else ("http" if "http" in s else ("sse" if "sse" in s else "?"))
          out(f"  - {nm} ({kind})")
        except Exception:
          continue
      if len(servers) > 200:
        out(f"  … (+{len(servers) - 200} more)")
      out()
      return ReplSlashResult(skip_model_turn=True)

    # Default: status.
    out("MCP:")
    out(f"  mcp.json: {mcp_path} ({'exists' if mcp_path.is_file() else 'missing'})")
    if parse_error:
      out(f"  parse_error: {parse_error}")
    out(f"  configured_servers: {len(servers)}")
    suffix = f"  (prefixes: {', '.join(sorted(loaded_prefixes))})" if loaded_prefixes else ""
    out(f"  loaded_toolsets:    {loaded_count}{suffix}")
    out()
    if not mcp_path.is_file():
      out("Tip: create .gemcode/mcp.json to enable MCP toolsets for this project.")
      out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /automations (local scheduled jobs for Kaira) ──────────────────────────
  if name in ("automations", "automation", "auto"):
    args_a = (sc.args or "").strip()
    parts = args_a.split() if args_a else []
    sub = (parts[0].strip().lower() if parts else "status")
    a_dir = cfg.project_root / ".gemcode" / "automations"
    a_state = a_dir / "state.json"

    def _bool_env(name: str) -> bool:
      return os.environ.get(name, "0").strip().lower() in ("1", "true", "yes", "on")

    if sub in ("help", "?"):
      out("Usage:")
      out("  /automations                 Status (enabled, count, state file)")
      out("  /automations list            List .gemcode/automations/*.json")
      out("  /automations on|off          Enable/disable local scheduling (sets GEMCODE_AUTOMATIONS)")
      out("  /automations init <name>     Create a starter automation json")
      out("  /automations run <name>      Enqueue an automation now via Kaira IPC (if running)")
      out("  /automations heartbeat off")
      out("  /automations heartbeat <seconds> [prompt...]   Set heartbeat interval + optional prompt")
      out()
      out("Paths:")
      out(f"  dir  : {a_dir}")
      out(f"  state: {a_state}")
      out()
      return ReplSlashResult(skip_model_turn=True)

    if sub in ("on", "enable", "enabled"):
      os.environ["GEMCODE_AUTOMATIONS"] = "1"
      out("automations: on  (GEMCODE_AUTOMATIONS=1)")
      out("Note: requires a running Kaira daemon (external or embedded) to execute.")
      out()
      return ReplSlashResult(skip_model_turn=True)
    if sub in ("off", "disable", "disabled"):
      os.environ["GEMCODE_AUTOMATIONS"] = "0"
      out("automations: off  (GEMCODE_AUTOMATIONS=0)")
      out()
      return ReplSlashResult(skip_model_turn=True)

    if sub == "heartbeat":
      if len(parts) >= 2 and parts[1].strip().lower() in ("off", "disable", "clear", "0"):
        os.environ["GEMCODE_KAIRA_HEARTBEAT_EVERY_S"] = "0"
        os.environ.pop("GEMCODE_KAIRA_HEARTBEAT_PROMPT", None)
        out("heartbeat: off")
        out()
        return ReplSlashResult(skip_model_turn=True)
      if len(parts) < 2:
        cur = int(os.environ.get("GEMCODE_KAIRA_HEARTBEAT_EVERY_S", "0") or "0")
        pr = os.environ.get("GEMCODE_KAIRA_HEARTBEAT_PROMPT", "") or ""
        out(f"heartbeat_every_s: {cur}")
        if pr:
          out(f"heartbeat_prompt:  {pr}")
        out()
        out("Set: /automations heartbeat 240 Heartbeat: summarise running jobs")
        out("Off: /automations heartbeat off")
        out()
        return ReplSlashResult(skip_model_turn=True)
      try:
        seconds = int(parts[1])
      except ValueError:
        seconds = 0
      if seconds <= 0:
        out("heartbeat: invalid seconds (use integer > 0)")
        out()
        return ReplSlashResult(skip_model_turn=True)
      os.environ["GEMCODE_AUTOMATIONS"] = "1"
      os.environ["GEMCODE_KAIRA_HEARTBEAT_EVERY_S"] = str(seconds)
      rest = args_a.split(None, 2)
      if len(rest) >= 3 and rest[2].strip():
        os.environ["GEMCODE_KAIRA_HEARTBEAT_PROMPT"] = rest[2].strip()
      out(f"heartbeat: on  (every {seconds}s)")
      out()
      return ReplSlashResult(skip_model_turn=True)

    if sub in ("init", "new") and len(parts) >= 2:
      name_raw = parts[1].strip().lower()
      import re

      if not re.fullmatch(r"[a-z0-9][a-z0-9-_]{0,63}", name_raw):
        out("Invalid name. Use lowercase letters/numbers plus - or _ (max 64 chars).")
        out()
        return ReplSlashResult(skip_model_turn=True)
      a_dir.mkdir(parents=True, exist_ok=True)
      p = a_dir / f"{name_raw}.json"
      if p.exists():
        out(f"Already exists: {p}")
        out()
        return ReplSlashResult(skip_model_turn=True)
      template = {
        "name": name_raw,
        "enabled": True,
        "priority": 0,
        "prompt": "Describe exactly what to do and what success looks like.",
        "triggers": [{"kind": "nightly", "at": "02:00"}],
      }
      try:
        import json

        p.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
      except Exception as e:
        out(f"Failed to write: {e}")
        out()
        return ReplSlashResult(skip_model_turn=True)
      out(f"Created: {p}")
      out("Enable runner-side execution with: gemcode kaira --automations (or GEMCODE_AUTOMATIONS=1)")
      out()
      return ReplSlashResult(skip_model_turn=True)

    if sub in ("run",) and len(parts) >= 2:
      target = parts[1].strip().lower()
      cfgs = {}
      try:
        from gemcode.automations import load_automations

        for a in load_automations(cfg.project_root):
          cfgs[a.name.lower()] = a
      except Exception:
        cfgs = {}
      a = cfgs.get(target)
      if a is None:
        out(f"Unknown automation: {target}")
        out("Tip: /automations list")
        out()
        return ReplSlashResult(skip_model_turn=True)
      # Enqueue via Kaira IPC.
      sock = os.environ.get("GEMCODE_KAIRA_SOCKET") or str(cfg.project_root / ".gemcode" / "ipc.sock")
      try:
        from gemcode.kaira_client import KairaIpcClient

        client = await KairaIpcClient.connect(socket_path=sock)
        try:
          res = await client.request(action="enqueue", prompt=a.prompt, priority=a.priority, session_id=(a.session_id or session_id))
        finally:
          await client.close()
        if not res.get("ok"):
          out(f"[kaira] {res.get('error') or 'enqueue failed'}")
        else:
          out(f"[kaira] enqueued: {res.get('job_id')}")
        out()
        return ReplSlashResult(skip_model_turn=True)
      except Exception as e:
        out(f"[kaira] IPC unavailable: {type(e).__name__}: {e}")
        out("Start Kaira with: gemcode kaira -C . --automations")
        out()
        return ReplSlashResult(skip_model_turn=True)

    if sub in ("list", "ls", "show"):
      try:
        from gemcode.automations import load_automations

        autos = load_automations(cfg.project_root)
      except Exception:
        autos = []
      out(f"automations_enabled: {_bool_env('GEMCODE_AUTOMATIONS')}")
      out(f"dir: {a_dir} ({'exists' if a_dir.is_dir() else 'missing'})")
      out(f"state: {a_state} ({'exists' if a_state.is_file() else 'missing'})")
      if not autos:
        out("(no automation configs found)")
        out()
        return ReplSlashResult(skip_model_turn=True)
      out("Configs:")
      for a in autos[:200]:
        trig = ", ".join(t.key() for t in a.triggers) if a.triggers else "(no triggers)"
        out(f"  - {a.name}\tenabled={a.enabled}\tpriority={a.priority}\t{trig}")
      if len(autos) > 200:
        out(f"  … (+{len(autos) - 200} more)")
      out()
      return ReplSlashResult(skip_model_turn=True)

    # status default
    try:
      from gemcode.automations import load_automations

      autos2 = load_automations(cfg.project_root)
    except Exception:
      autos2 = []
    out(f"automations_enabled: {_bool_env('GEMCODE_AUTOMATIONS')}")
    out(f"configs: {len(autos2)}  (dir: {a_dir})")
    out(f"state_file: {a_state} ({'exists' if a_state.is_file() else 'missing'})")
    out()
    out("Tip: /automations list  ·  Enable: gemcode kaira --automations  ·  Help: /automations help")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /afc (Automatic Function Calling UX) ───────────────────────────────────
  if name == "afc":
    args_f = (sc.args or "").strip()
    parts = args_f.split() if args_f else []
    sub = (parts[0].strip().lower() if parts else "status")

    def _norm(v: str) -> str:
      return (v or "").strip().lower()

    if sub in ("help", "?"):
      out("Usage:")
      out("  /afc                 Show AFC prompt settings")
      out("  /afc default all|callables|clear   Set GEMCODE_AFC_DEFAULT")
      out("  /afc prompt on|off                Set GEMCODE_AFC_PROMPT")
      out()
      out("Notes:")
      out("  These affect runner construction; GemCode will rebuild runner on next turn.")
      out()
      return ReplSlashResult(skip_model_turn=True)

    if sub == "default":
      if len(parts) < 2:
        out(f"GEMCODE_AFC_DEFAULT: {os.environ.get('GEMCODE_AFC_DEFAULT', '(unset)')}")
        out()
        return ReplSlashResult(skip_model_turn=True)
      v = _norm(parts[1])
      if v in ("clear", "unset", "off", "none"):
        os.environ.pop("GEMCODE_AFC_DEFAULT", None)
        out("GEMCODE_AFC_DEFAULT: (unset)")
        out("Runner will rebuild on next turn.")
        out()
        return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
      if v not in ("all", "callables"):
        out("Invalid. Use: all|callables|clear")
        out()
        return ReplSlashResult(skip_model_turn=True)
      os.environ["GEMCODE_AFC_DEFAULT"] = v
      out(f"GEMCODE_AFC_DEFAULT: {v}")
      out("Runner will rebuild on next turn.")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)

    if sub == "prompt":
      if len(parts) < 2:
        out(f"GEMCODE_AFC_PROMPT: {os.environ.get('GEMCODE_AFC_PROMPT', '(unset => default on)')}")
        out()
        return ReplSlashResult(skip_model_turn=True)
      v2 = _norm(parts[1])
      if v2 in ("on", "1", "true", "yes"):
        os.environ["GEMCODE_AFC_PROMPT"] = "1"
      elif v2 in ("off", "0", "false", "no"):
        os.environ["GEMCODE_AFC_PROMPT"] = "0"
      else:
        out("Invalid. Use: on|off")
        out()
        return ReplSlashResult(skip_model_turn=True)
      out(f"GEMCODE_AFC_PROMPT: {os.environ.get('GEMCODE_AFC_PROMPT')}")
      out("Runner will rebuild on next turn.")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)

    out("AFC:")
    out(f"  GEMCODE_AFC_PROMPT : {os.environ.get('GEMCODE_AFC_PROMPT', '(unset => default on)')}")
    out(f"  GEMCODE_AFC_DEFAULT: {os.environ.get('GEMCODE_AFC_DEFAULT', '(unset)')}")
    out()
    return ReplSlashResult(skip_model_turn=True)

  if name == "tools":
    args_t = (sc.args or "").strip().lower()
    if args_t in ("smoke", "decl", "declarations"):
      from gemcode.tools_inspector import inspect_tools, smoke_tools

      inspections = inspect_tools(cfg, extra_tools=extra_tools)
      bad = smoke_tools(inspections)
      if not bad:
        out(f"tools smoke: OK ({len(inspections)} tools, declarations compile)")
      else:
        out(f"tools smoke: {len(bad)} failure(s) of {len(inspections)} tools")
        for i in bad[:60]:
          out(f"  {i.name}: {i.declaration_error}")
        if len(bad) > 60:
          out(f"  … ({len(bad) - 60} more)")
      out()
      return ReplSlashResult(skip_model_turn=True)
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
    _lg = getattr(cfg, "session_loaded_skill_names", None) or []
    out(f"loaded_skills:  {', '.join(_lg) if _lg else '(none)'}  (/gemskill)")
    _pq = getattr(cfg, "pending_attachment_paths", None) or []
    out(f"queued_attachments: {len(_pq)}  (/attach list)")
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
      prof = getattr(cfg, "_policy_profile", None)
      out("  Dynamic policy:")
      out(f"    dynamic_token_policy:  {getattr(cfg, 'dynamic_token_policy', True)}")
      out(f"    dynamic_risk_policy:   {getattr(cfg, 'dynamic_risk_policy', True)}")
      out(f"    dynamic_risk_boost:    {getattr(cfg, 'dynamic_risk_boost', 0.6)}")
      out()
      out("  ADK advanced:")
      out(f"    adk_agent_transfer:    {getattr(cfg, 'enable_adk_agent_transfer', True)}")
      out(f"    adk_events_compaction: {getattr(cfg, 'enable_adk_events_compaction', False)}")
      if getattr(cfg, "enable_adk_events_compaction", False):
        out(f"    compaction_interval:   {getattr(cfg, 'adk_compaction_interval', 6)}")
        out(f"    compaction_overlap:    {getattr(cfg, 'adk_compaction_overlap', 1)}")
        out(f"    compaction_model:      {getattr(cfg, 'adk_compaction_summarizer_model', 'gemini-2.5-flash')}")
      out(f"    risk_score:            {risk:.2f}")
      if isinstance(pct, int):
        out(f"    context_percent_left:  {pct}%")
      if isinstance(prof, dict):
        try:
          out(f"    profile.failure_rate_ema: {float(prof.get('failure_rate_ema', 0.0) or 0.0):.2f}")
          out(f"    profile.files_touched_ema: {float(prof.get('files_touched_ema', 0.0) or 0.0):.2f}")
        except Exception:
          pass
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
      _clear_session_loaded_skills(cfg)
      cfg.pending_attachment_paths.clear()
      new_id = str(uuid.uuid4())
      out(f"new session_id: {new_id}")
      out()
      return ReplSlashResult(
          skip_model_turn=True,
          new_session_id=new_id,
          force_rebuild_runner=True,
      )

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
      _clear_session_loaded_skills(cfg)
      cfg.pending_attachment_paths.clear()
      out(f"Resuming session {found[:8]}…")
      out()
      return ReplSlashResult(
          skip_model_turn=True,
          new_session_id=found,
          force_rebuild_runner=True,
      )

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

    # Prompt composition breakdown (chars; token estimate is approximate).
    try:
      from gemcode.skills import build_skill_manifest_text
      from gemcode.output_styles import build_output_style_section
      from gemcode.rules import build_rules_section
      from gemcode.agent import _load_gemini_md  # internal helper

      touched = sorted(getattr(cfg, "_touched_paths", set()) or set())
      style_txt = build_output_style_section(cfg.project_root, getattr(cfg, "output_style", None))
      rules_txt = build_rules_section(cfg.project_root, touched_paths=touched or None)
      skills_txt = build_skill_manifest_text(cfg.project_root)
      gemini_txt = _load_gemini_md(cfg.project_root)

      def _tok(ch: int) -> int:
        return int(ch / 4)  # rough heuristic

      out("")
      out("prompt_breakdown (approx):")
      out(f"  output_style_chars: {len(style_txt)}  (~{_tok(len(style_txt))} tok)")
      out(f"  rules_chars       : {len(rules_txt)}  (~{_tok(len(rules_txt))} tok)")
      out(f"  skills_manifest   : {len(skills_txt)}  (~{_tok(len(skills_txt))} tok)")
      out(f"  GEMINI_md         : {len(gemini_txt)}  (~{_tok(len(gemini_txt))} tok)")
      if touched:
        preview = ", ".join(touched[:10]) + ("…" if len(touched) > 10 else "")
        out(f"  touched_paths     : {len(touched)} ({preview})")
      else:
        out("  touched_paths     : 0")
    except Exception:
      pass

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

  if name in ("summarise", "summarize"):
    focus = (sc.args or "").strip()
    out("Summarising current session into durable memory…")
    if focus:
      out(f"Focus: {focus}")
    out()
    try:
      model = (
        getattr(cfg, "adk_compaction_summarizer_model", None)
        or getattr(cfg, "model", "")
        or "gemini-2.5-flash"
      )
      result = summarise_session(
        cfg.project_root,
        session_id=session_id,
        model=model,
        focus=focus,
      )
    except Exception as e:
      out(f"[gemcode] session summarise failed: {e}")
      out()
      return ReplSlashResult(skip_model_turn=True)

    if result.get("error"):
      out(f"[gemcode] {result['error']}")
      out()
      return ReplSlashResult(skip_model_turn=True)

    out(f"Saved summary: {result.get('summary_path')}")
    mem_saved = len(result.get("memory_facts_saved") or [])
    user_saved = len(result.get("user_facts_saved") or [])
    open_items = len(result.get("open_items") or [])
    out(f"Curated memory saved: project={mem_saved}, user={user_saved}, open_items={open_items}")
    if result.get("notes_status"):
      out(f"Notes: {result.get('notes_status')}")
    out("Starting a fresh session so the next turn stays lightweight.")
    out()
    _clear_session_loaded_skills(cfg)
    cfg.pending_attachment_paths.clear()
    new_id = str(uuid.uuid4())
    return ReplSlashResult(
        skip_model_turn=True,
        new_session_id=new_id,
        force_rebuild_runner=True,
    )

  if name in ("exit", "quit"):
    return ReplSlashResult(exit_repl=True)

  # ── /kaira ───────────────────────────────────────────────────────────────
  if name == "kaira":
    args_s = (sc.args or "").strip()
    # Control-plane subcommands via IPC.
    if args_s:
      parts = args_s.split()
      sub = parts[0].strip().lower()
      sock = os.environ.get("GEMCODE_KAIRA_SOCKET") or str(cfg.project_root / ".gemcode" / "ipc.sock")
      if sub in ("follow",) and len(parts) >= 2:
        job_id = parts[1].strip()
        os.environ["GEMCODE_KAIRA_FOLLOW_JOB"] = job_id
        out(f"[kaira] follow: {job_id}")
        out()
        return ReplSlashResult(skip_model_turn=True)
      if sub in ("unfollow", "unfollow-all", "unfollowall", "clear-follow", "clearfollow"):
        try:
          os.environ.pop("GEMCODE_KAIRA_FOLLOW_JOB", None)
        except Exception:
          pass
        out("[kaira] follow cleared")
        out()
        return ReplSlashResult(skip_model_turn=True)
      if sub in ("jobs", "list"):
        try:
          from gemcode.kaira_client import KairaIpcClient
          client = await KairaIpcClient.connect(socket_path=sock)
          try:
            res = await client.request(action="list_jobs", limit=30)
          finally:
            await client.close()
          if not res.get("ok"):
            out(f"[kaira] {res.get('error') or 'list_jobs failed'}")
            out()
            return ReplSlashResult(skip_model_turn=True)
          out("Kaira jobs:")
          for j in res.get("jobs") or []:
            if not isinstance(j, dict):
              continue
            jid = str(j.get("job_id") or "")[:12]
            st = str(j.get("status") or "")
            pr = j.get("priority")
            out(f"  - {jid}\t{st}\tpriority={pr}")
          out()
          return ReplSlashResult(skip_model_turn=True)
        except Exception as e:
          out(f"[kaira] IPC unavailable: {type(e).__name__}: {e}")
          out()
          return ReplSlashResult(skip_model_turn=True)

      if sub in ("job", "show") and len(parts) >= 2:
        job_id = parts[1].strip()
        try:
          from gemcode.kaira_client import KairaIpcClient
          client = await KairaIpcClient.connect(socket_path=sock)
          try:
            res = await client.request(action="get_job", job_id=job_id)
          finally:
            await client.close()
          if not res.get("ok"):
            out(f"[kaira] {res.get('error') or 'get_job failed'}")
            out()
            return ReplSlashResult(skip_model_turn=True)
          j = res.get("job") or {}
          if isinstance(j, dict):
            out(f"job_id: {j.get('job_id')}")
            out(f"status: {j.get('status')}")
            out(f"priority: {j.get('priority')}")
            out(f"session_id: {j.get('session_id')}")
            txt = str(j.get('last_text') or '')
            if txt:
              out("")
              out(txt[:2000])
          out()
          return ReplSlashResult(skip_model_turn=True)
        except Exception as e:
          out(f"[kaira] IPC unavailable: {type(e).__name__}: {e}")
          out()
          return ReplSlashResult(skip_model_turn=True)

      if sub in ("cancel", "kill") and len(parts) >= 2:
        job_id = parts[1].strip()
        try:
          from gemcode.kaira_client import KairaIpcClient
          client = await KairaIpcClient.connect(socket_path=sock)
          try:
            res = await client.request(action="cancel_job", job_id=job_id)
          finally:
            await client.close()
          if not res.get("ok"):
            out(f"[kaira] {res.get('error') or 'cancel failed'}")
          else:
            out(f"[kaira] cancelled: {job_id}")
          out()
          return ReplSlashResult(skip_model_turn=True)
        except Exception as e:
          out(f"[kaira] IPC unavailable: {type(e).__name__}: {e}")
          out()
          return ReplSlashResult(skip_model_turn=True)

    out("Kaira — background parallel job scheduler")
    out()
    out("What it does:")
    out("  Runs a long-lived daemon that accepts prompts on stdin and executes")
    out("  each as an isolated GemCode agent job. Jobs run concurrently (up to")
    out("  --concurrency N, default 2) in a priority queue.")
    out()
    out("How to launch (in a separate terminal):")
    out(f"  gemcode kaira -C {cfg.project_root}")
    out("  gemcode kaira -C <project> --concurrency 4 --yes")
    out("  gemcode kaira -C <project> --model gemini-2.5-pro --deep-research")
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
    out("  kaira_sleep_ms(duration_ms)   — pause this job without blocking others")
    out("  kaira_enqueue_prompt(prompt, priority, session_id)")
    out("                                  — the model can queue MORE jobs itself")
    out()
    out("Use cases:")
    out("  - Process N files in parallel: each file → one job")
    out("  - Polling loop: job sleeps, re-enqueues itself with kaira_enqueue_prompt")
    out("  - Bulk code generation, test runs, or research across many documents")
    out("  - Background work while you continue chatting in this session")
    out()
    out("In the TUI:")
    out("  /kaira follow <job_id_prefix>   Focus output on one job")
    out("  /kaira unfollow                Clear focus filter")
    out()
    return ReplSlashResult(skip_model_turn=True)

  # ── /org, /hire, /delegate (org hierarchy + delegation) ──────────────────
  if name in ("org", "hire", "delegate", "assign", "spawn"):
    args = (sc.args or "").strip()
    if name == "org":
      out("Org chart (members):")
      out("  /org tree")
      out("  /org list")
      out("  /org hire <name> <title> [kaira_worker|subagent] [reports_to] [description...]")
      out("  /org assign <member> <task...>     (alias: /delegate)")
      out("  /org spawn <name> <title> <kind> <task...>   (hire + assign)")
      out("  /org improve <member> <lessons...>           (append to member skill)")
      out()
      if not args or args.lower() in ("help", "?"):
        return ReplSlashResult(skip_model_turn=True)
      sub = args.lower().split()[0]
      if sub == "list":
        from gemcode.org import list_members

        ms = list_members(cfg.project_root)
        if not ms:
          out("(no members)")
        else:
          for m in ms:
            boss = f" reports_to={m.reports_to}" if getattr(m, "reports_to", "") else ""
            out(f"  - {m.name} ({m.title}) [{m.kind}] id={m.id}{boss}")
        out()
        return ReplSlashResult(skip_model_turn=True)
      if sub == "tree":
        try:
          from gemcode.org import org_tree
          import json as _json
          out(_json.dumps(org_tree(cfg.project_root), ensure_ascii=False, indent=2))
        except Exception:
          out("(failed to render org tree)")
        out()
        return ReplSlashResult(skip_model_turn=True)
      if sub == "hire":
        # /org hire <name> <title> [kind] [reports_to] [desc...]
        rest = args.split(maxsplit=1)[1] if len(args.split()) > 1 else ""
        if not rest:
          out("Usage: /org hire <name> <title> [kaira_worker|subagent] [reports_to] [description...]")
          out()
          return ReplSlashResult(skip_model_turn=True)
        parts = rest.split()
        if len(parts) < 2:
          out("Usage: /org hire <name> <title> [kaira_worker|subagent] [reports_to] [description...]")
          out()
          return ReplSlashResult(skip_model_turn=True)
        nm = parts[0]
        title = parts[1]
        kind = "subagent"
        reports_to = "manager"
        desc = ""
        i = 2
        if len(parts) > i and parts[i] in ("kaira_worker", "subagent"):
          kind = parts[i]
          i += 1
        if len(parts) > i:
          reports_to = parts[i]
          i += 1
        desc = " ".join(parts[i:]).strip() if len(parts) > i else ""
        from gemcode.org import hire_member
        m = hire_member(cfg.project_root, name=nm, title=title, kind=kind, reports_to=reports_to, description=desc)  # type: ignore[arg-type]
        out(f"Hired: {m.name} ({m.title}) [{m.kind}] reports_to={m.reports_to} id={m.id}")
        out()
        return ReplSlashResult(skip_model_turn=True)
      if sub in ("assign", "delegate"):
        rest = args.split(maxsplit=1)[1] if len(args.split()) > 1 else ""
        if not rest:
          out("Usage: /org assign <member> <task...>")
          out()
          return ReplSlashResult(skip_model_turn=True)
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
          out("Usage: /org assign <member> <task...>")
          out()
          return ReplSlashResult(skip_model_turn=True)
        member = parts[0].strip()
        task = parts[1].strip()
        prompt = (
          "Assign this task using org_delegate(member, task). "
          "If delegation returns a job_id, tell the user it's running in background.\n\n"
          f"member={member}\n"
          f"task={task}\n"
        )
        return ReplSlashResult(model_prompt=prompt)
      if sub == "spawn":
        # /org spawn <name> <title> <kind> <task...>
        rest = args.split(maxsplit=1)[1] if len(args.split()) > 1 else ""
        parts = rest.split(maxsplit=3) if rest else []
        if len(parts) < 4:
          out("Usage: /org spawn <name> <title> <kaira_worker|subagent> <task...>")
          out()
          return ReplSlashResult(skip_model_turn=True)
        nm, title, kind, task = parts[0], parts[1], parts[2], parts[3]
        prompt = (
          "Spawn and assign using org_spawn(name,title,kind,task). "
          "If delegation returns a job_id, tell the user it's running in background.\n\n"
          f"name={nm}\n"
          f"title={title}\n"
          f"kind={kind}\n"
          f"task={task}\n"
        )
        return ReplSlashResult(model_prompt=prompt)

      if sub == "improve":
        rest = args.split(maxsplit=1)[1] if len(args.split()) > 1 else ""
        parts2 = rest.split(maxsplit=1) if rest else []
        if len(parts2) < 2:
          out("Usage: /org improve <member> <lessons...>")
          out()
          return ReplSlashResult(skip_model_turn=True)
        member = parts2[0].strip()
        lessons = parts2[1].strip()
        prompt = (
          "Improve the member's skill based on these lessons.\n"
          "Call org_improve(member, lessons) and confirm the skill path.\n\n"
          f"member={member}\n"
          f"lessons={lessons}\n"
        )
        return ReplSlashResult(model_prompt=prompt)

      out("Unknown /org subcommand. Try: /org tree  /org list  /org hire  /org assign  /org spawn")
      out()
      return ReplSlashResult(skip_model_turn=True)

    if name == "hire":
      if not args:
        out("Usage: /hire <name> <title> [kaira_worker|subagent] [description...]")
        out()
        return ReplSlashResult(skip_model_turn=True)
      parts = args.split()
      if len(parts) < 2:
        out("Usage: /hire <name> <title> [kaira_worker|subagent] [description...]")
        out()
        return ReplSlashResult(skip_model_turn=True)
      nm = parts[0]
      title = parts[1]
      kind = "subagent"
      desc = ""
      if len(parts) >= 3 and parts[2] in ("kaira_worker", "subagent"):
        kind = parts[2]
        desc = " ".join(parts[3:]).strip()
      else:
        desc = " ".join(parts[2:]).strip()
      from gemcode.org import hire_member

      m = hire_member(cfg.project_root, name=nm, title=title, kind=kind, description=desc)  # type: ignore[arg-type]
      out(f"Hired: {m.name} ({m.title}) [{m.kind}] id={m.id}")
      out()
      return ReplSlashResult(skip_model_turn=True)

    if name in ("delegate", "assign"):
      if not args:
        out("Usage: /delegate <member> <task...>")
        out()
        return ReplSlashResult(skip_model_turn=True)
      parts = args.split(maxsplit=1)
      if len(parts) < 2:
        out("Usage: /delegate <member> <task...>")
        out()
        return ReplSlashResult(skip_model_turn=True)
      member = parts[0].strip()
      task = parts[1].strip()
      # Route through tool so it can use Kaira IPC or subagent as appropriate.
      prompt = (
        "Delegate this task using org_delegate(member, task). "
        "If delegation returns a job_id, tell the user it's running in background.\n\n"
        f"member={member}\n"
        f"task={task}\n"
      )
      return ReplSlashResult(model_prompt=prompt)

    if name == "spawn":
      if not args:
        out("Usage: /spawn <name> <title> <kaira_worker|subagent> <task...>")
        out()
        return ReplSlashResult(skip_model_turn=True)
      parts = args.split(maxsplit=3)
      if len(parts) < 4:
        out("Usage: /spawn <name> <title> <kaira_worker|subagent> <task...>")
        out()
        return ReplSlashResult(skip_model_turn=True)
      nm, title, kind, task = parts[0], parts[1], parts[2], parts[3]
      prompt = (
        "Spawn and assign using org_spawn(name,title,kind,task). "
        "If delegation returns a job_id, tell the user it's running in background.\n\n"
        f"name={nm}\n"
        f"title={title}\n"
        f"kind={kind}\n"
        f"task={task}\n"
      )
      return ReplSlashResult(model_prompt=prompt)

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

  # ── /maps (Maps grounding, deep-research stack) ────────────────────────────
  if name in ("maps", "map"):
    args_s = (sc.args or "").strip().lower()
    if not args_s or args_s in ("status", "show"):
      status = "on  ✓" if cfg.enable_maps_grounding else "off"
      out(f"maps_grounding: {status}")
      out()
      out("Commands: /maps on  ·  /maps off")
      out("When on: Maps-backed grounding is available alongside deep-research tools (if enabled).")
      out()
      return ReplSlashResult(skip_model_turn=True)
    if args_s == "on":
      cfg.enable_maps_grounding = True
      out("maps_grounding: on")
      out("  Runner will rebuild on next turn.")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    if args_s == "off":
      cfg.enable_maps_grounding = False
      out("maps_grounding: off")
      out()
      return ReplSlashResult(skip_model_turn=True, force_rebuild_runner=True)
    out(f"Unknown /maps subcommand: '{args_s}'")
    out("Usage: /maps [on|off]")
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

  # Unknown slash command: if it matches a skill name, invoke it.
  metas = discover_skill_metas(cfg.project_root)
  if name in metas:
    s = load_skill(cfg.project_root, name)
    if s is not None:
      expanded = expand_skill_text(s, arguments=(sc.args or ""), session_id=session_id)
      files = list_supporting_files(s)
      prompt = (
        f"Apply GemSkill `/{s.meta.name}`.\n\n"
        f"## Skill instructions\n{expanded}\n\n"
        + (f"## Skill supporting files\n{', '.join(files)}\n\n" if files else "")
        + "Now proceed."
      )
      return ReplSlashResult(model_prompt=prompt)

  out(f"Unknown command: /{sc.command_name}")
  out("Try /help")
  out()
  return ReplSlashResult(skip_model_turn=True)
