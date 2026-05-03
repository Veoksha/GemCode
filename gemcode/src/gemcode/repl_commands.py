"""
Built-in REPL slash-command handlers (interactive CLI–style thin wrappers).

Keeps `cli.py` smaller and makes output testable.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Any, Iterable

from gemcode.config import GemCodeConfig
from gemcode.trust import is_trusted_root
from gemcode.version import get_version


def _is_executable(p: Path) -> bool:
  try:
    return p.is_file() and (p.stat().st_mode & stat.S_IXUSR)
  except OSError:
    return False


def format_doctor_lines(cfg: GemCodeConfig) -> list[str]:
  lines = [
      "Doctor (environment sanity check):",
      f"  python: {sys.version.split()[0]}",
  ]
  try:
    import google.genai  # noqa: F401

    lines.append("  google.genai: import ok")
  except ImportError:
    lines.append("  google.genai: MISSING (pip install google-genai)")
  key = os.environ.get("GOOGLE_API_KEY")
  lines.append(f"  GOOGLE_API_KEY: {'set' if key else 'MISSING'}")
  try:
    from gemcode.credentials import credentials_path

    cp = credentials_path()
    lines.append(
        f"  credentials_file: {cp} "
        f"{'(exists)' if cp.is_file() else '(missing)'}"
    )
  except Exception:
    pass
  try:
    root = cfg.project_root.resolve()
    lines.append(f"  project_root: {root}")
  except Exception as e:
    lines.append(f"  project_root: ERROR {e}")
  lines.append(f"  folder_trusted: {is_trusted_root(cfg.project_root)}")
  lines.append(
      f"  gemcode_version: {os.environ.get('GEMCODE_VERSION', get_version())}"
  )
  return lines


def format_model_lines(cfg: GemCodeConfig) -> list[str]:
  return [
      "Model:",
      f"  effective_model: {cfg.model}",
      f"  GEMCODE_MODEL_MODE: {getattr(cfg, 'model_mode', '')}",
      f"  GEMCODE_MODEL_FAMILY_MODE: {getattr(cfg, 'model_family_mode', '')}",
      f"  model_overridden: {getattr(cfg, 'model_overridden', False)}",
  ]


def format_permissions_lines(cfg: GemCodeConfig) -> list[str]:
  from gemcode.permissions import describe_rules
  rules_lines = describe_rules(cfg.project_root)
  settings_file = cfg.project_root / ".gemcode" / "settings.json"
  global_settings = os.path.expanduser("~/.gemcode/settings.json")
  ac = cfg.allow_commands
  if ac is None:
    preview = "(default allowlist)"
  else:
    names = sorted(ac)
    preview = ", ".join(names[:16])
    if len(names) > 16:
      preview += f", … (+{len(names) - 16} more)"
  lines = [
      "Permissions:",
      f"  permission_mode       : {cfg.permission_mode}",
      f"  yes_to_all            : {cfg.yes_to_all}",
      f"  interactive_permission: {getattr(cfg, 'interactive_permission_ask', False)}",
      f"  hitl_sticky_session   : {getattr(cfg, 'interactive_hitl_sticky_session', True)}",
      f"  allow_commands        : {preview}",
      "",
      "  Settings-based rules (deny first, then allow):",
      f"  Project: {settings_file} ({'exists' if settings_file.exists() else 'not found'})",
      f"  Global : {global_settings} ({'exists' if os.path.exists(global_settings) else 'not found'})",
  ]
  lines.extend(rules_lines)
  lines.append("")
  lines.append("  To add rules, create .gemcode/settings.json:")
  lines.append('  { "permissions": { "allow": ["bash(git *)"], "deny": ["bash(rm -rf *)"] } }')
  return lines


def format_memory_lines(cfg: GemCodeConfig) -> list[str]:
  enabled = bool(getattr(cfg, "enable_memory", False))
  mem_path = cfg.project_root / ".gemcode" / "memories.jsonl"
  return [
      "Memory:",
      f"  GEMCODE_ENABLE_MEMORY: {enabled}",
      f"  memories_file: {mem_path}",
      f"  file_exists: {mem_path.is_file()}",
  ]


def format_hooks_lines(cfg: GemCodeConfig) -> list[str]:
  env_hook = os.environ.get("GEMCODE_POST_TURN_HOOK")
  hooks_dir = cfg.project_root / ".gemcode" / "hooks"
  default_hook = hooks_dir / "post_turn"
  active: str | None = env_hook
  if not active and _is_executable(default_hook):
    active = str(default_hook)
  lines = [
      "Hooks:",
      f"  GEMCODE_POST_TURN_HOOK: {env_hook or '(unset)'}",
      f"  default_path: {default_hook}",
      f"  default_executable: {_is_executable(default_hook)}",
  ]
  if active:
    lines.append(f"  active_hook: {active}")
  else:
    lines.append("  active_hook: (none — set env or chmod +x .gemcode/hooks/post_turn)")

  # ── Shell lifecycle hooks (like reference terminal UI PreToolUse / PostToolUse) ──────
  lines.append("")
  lines.append("Shell lifecycle hooks (.gemcode/hooks/):")
  lines.append("  Scripts run at tool/session lifecycle points.")
  lifecycle_hooks = [
      ("pre_tool_use",   "Run BEFORE each tool call. Non-zero exit → tool denied."),
      ("post_tool_use",  "Run AFTER each tool call. Informational; return ignored."),
      ("session_start",  "Run when a GemCode session starts."),
      ("session_stop",   "Run when a GemCode session ends."),
  ]
  for hook_name, description in lifecycle_hooks:
    found = None
    for ext in ("", ".sh", ".py", ".bash"):
      p = hooks_dir / f"{hook_name}{ext}"
      if p.is_file() and _is_executable(p):
        found = p
        break
    status = f"✓ {found}" if found else "✗ not found (create and chmod +x to enable)"
    lines.append(f"  {hook_name:20s}  {status}")
    lines.append(f"  {'':20s}  {description}")
  lines.append("")
  lines.append(f"  Hook directory: {hooks_dir}")
  lines.append("  Hooks receive JSON on stdin with tool name, args, and result.")
  lines.append("  Set GEMCODE_HOOK_TYPE env var is set for each hook.")
  return lines


def format_audit_lines(cfg: GemCodeConfig, *, tail: int = 40) -> list[str]:
  """Last N lines of `.gemcode/audit.log` (JSON lines; truncated per line)."""
  path = cfg.project_root / ".gemcode" / "audit.log"
  if not path.is_file():
    return [
        "Audit:",
        f"  (no log yet at {path})",
    ]
  try:
    raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
  except OSError as e:
    return ["Audit:", f"  (read error: {e})"]
  chunk = raw[-tail:] if len(raw) > tail else raw
  lines = [f"Audit (showing last {len(chunk)} of {len(raw)} lines):"]
  for ln in chunk:
    if len(ln) > 420:
      ln = ln[:420] + "…"
    lines.append(f"  {ln}")
  return lines


def format_tools_lines(
    cfg: GemCodeConfig,
    *,
    extra_tools: Iterable[Any] | None = None,
) -> list[str]:
  from gemcode.tools_inspector import inspect_tools

  inspections = inspect_tools(cfg, extra_tools=extra_tools)
  lines = [f"Tools ({len(inspections)}):", ""]
  for i in inspections[:120]:
    decl = "decl_ok" if i.declaration_present else ("decl_err" if i.declaration_error else "no_decl")
    err = f" ({i.declaration_error})" if i.declaration_error else ""
    lines.append(f"  {i.name}\t{i.category}\t{i.tool_type}\t{decl}{err}")
  if len(inspections) > 120:
    lines.append(f"  … ({len(inspections) - 120} more)")
  return lines


# ---------------------------------------------------------------------------
# Slash command registry for TUI (prompt_toolkit) + plain REPL (readline Tab).
# One canonical name per feature where possible; a few shortcuts (e.g.
# ``gemskill``) stay as their own row so Tab/TUI shows them. Other aliases
# still work in ``process_repl_slash`` but are omitted here (see descriptions).
# ---------------------------------------------------------------------------
SLASH_COMMANDS: list[tuple[str, str]] = [
    ("add-dir",     "Extra read/search roots  ·  /add_dir works too"),
    ("append",      "Iterate a file  ·  /append gemskill <name> <request>"),
    ("audit",       "Tail audit.log  ·  /logs same"),
    ("autotune",    "Branch + eval ledger  ·  /autotune init <tag>  ·  /autotune eval"),
    ("batch",       "Built-in batch GemSkill (large parallel changes)"),
    ("caveman",     "Terse output mode  ·  /caveman lite|full|ultra|wenyan|off"),
    ("caveman:compress", "Compress memory file  ·  /caveman:compress <path> [lite|full|ultra]"),
    ("budget",      "Per-turn token budget  ·  /token-budget same"),
    ("caps",        "Capabilities  ·  /capabilities /capability same"),
    # NOTE: /clear is an alias of `/session new`; keep alias working but do not list it here.
    ("code",        "Toggle ADK BuiltInCodeExecutor (sandboxed Python)"),
    ("compact",     "Context compaction / summarization"),
    ("computer",    "Browser automation  ·  /browser same"),
    ("config",      "Dump active configuration"),
    ("context",     "Context pressure + token breakdown"),
    ("cost",        "Session token usage + estimated cost"),
    ("create",      "New GemSkill file  ·  /create gemskill <name> [description]"),
    ("gemskill",    "Load skill into session prompt  ·  /gemskill <name>  ·  list  ·  clear"),
    ("curated",     "Curated memory snapshot  ·  /memory-files /memoryfiles same"),
    ("diff",        "Git diff or checkpoint diff"),
    ("doctor",      "Environment sanity check"),
    ("embeddings",  "Semantic file search  ·  /embed same"),
    ("eval",        "Eval gates (tools + pytest)  ·  /eval llm optional"),
    ("exit",        "Leave the REPL  ·  /quit same"),
    ("help",        "Short help  ·  /? same"),
    ("hooks",       "Post-turn hook configuration"),
    ("attach",      "Queue file(s) for next message (PDF, images, …)  ·  /image /file /img  ·  list  ·  clear"),
    ("init",        "Generate gemcode.md project instructions"),
    # NOTE: /file /image /img are aliases of /attach; keep alias working but do not list them here.
    ("kaira",       "Background jobs — gemcode runtime (alias: gemcode kaira)"),
    ("runtime",     "Fleet socket status · gemcode runtime · attach/connect"),
    ("bus",         "Runtime bus — send/publish lightweight messages over IPC"),
    ("inbox",       "Bus inbox filters for this UI (to/topics)"),
    ("agent",       "Create/manage a child agent workspace (folder + registry)"),
    # NOTE: /org and /delegate are deprecated aliases; keep working but do not list.
    ("limits",      "Execution limits (calls, context, …)"),
    ("live-audio",  "How to run gemcode live-audio  ·  /liveaudio same"),
    ("login",       "How to run gemcode login (API key)"),
    ("maps",        "Maps grounding  ·  /maps on|off  ·  /map same"),
    ("memory",      "Persistent memory  ·  /memory on|off"),
    ("mode",        "Model mode: fast|balanced|quality|auto"),
    ("model",       "Model info / override  ·  /models same"),
    ("notes",       ".gemcode/notes.md  ·  /notes clear  ·  /notes edit"),
    ("permissions", "Permission + HITL  ·  /perm /permission same"),
    ("plan",        "Plan-before-act mode"),
    ("research",    "Deep research tools  ·  /research on|off"),
    ("review",      "Parallel code review"),
    ("rewind",      "Checkpoints  ·  /checkpoint same"),
    ("rules",       "Rule files from .gemcode/rules/"),
    ("session",     "Session id / list / resume / new"),
    # NOTE: /skill is an alias of /skills; keep alias working but do not list it here.
    ("skills",      "List GemSkills"),
    ("status",      "Model, capabilities, thinking, limits"),
    ("style",       "Output styles  ·  /style <name>|off"),
    ("super",       "Super mode  ·  auto-approve tools/shell, no HITL  ·  /super off"),
    ("summarise",   "Summarise current session, persist key points, then reset  ·  /summarize same"),
    ("thinking",    "Thinking verbose/brief/off, budget, level"),
    ("tools",       "Tool inventory  ·  /tools smoke"),
    ("trust",       "Workspace trust  ·  /trust on|off"),
    ("version",     "GemCode version"),
]


def install_readline_slash_completion() -> bool:
  """
  Enable Tab completion for slash commands in the plain REPL (``input("> ")``).

  Returns False if readline is unavailable or stdin is not a TTY.
  """
  try:
    import readline
  except ImportError:
    return False
  if not hasattr(sys.stdin, "isatty") or not sys.stdin.isatty():
    return False

  # Treat ``/foo`` as one word so Tab completes after ``/``.
  try:
    delims = readline.get_completer_delims()
    if "/" in delims:
      readline.set_completer_delims(delims.replace("/", ""))
  except Exception:
    pass

  ordered = [name for name, _ in SLASH_COMMANDS]
  _matches: list[str] = []

  def completer(text: str, state: int) -> str | None:
    nonlocal _matches
    if state == 0:
      _matches = []
      if not text.startswith("/"):
        return None
      # Only complete the first token (/command …); skip when typing args.
      if " " in text[1:]:
        return None
      frag = text[1:].lower()
      for n in ordered:
        if not frag or n.startswith(frag) or frag in n:
          _matches.append(f"/{n} ")
      if not _matches:
        return None
    try:
      return _matches[state]
    except IndexError:
      return None

  readline.set_completer(completer)
  # GNU readline: double-Tab lists matches. libedit (macOS) may ignore unknown "set" directives.
  for spec in (
      "set show-all-if-ambiguous on",
      "set completion-ignore-case on",
      "tab: complete",
  ):
    try:
      readline.parse_and_bind(spec)
    except Exception:
      pass
  return True


def slash_help_lines() -> list[str]:
  return [
      "Slash commands:",
      "  (CLI) gemcode -C DIR  Use a project folder as root (recommended vs. ~ )",
      "  (CLI) gemcode login   Save or change API key (~/.gemcode/credentials.json)",
      "",
      "  Project setup:",
      "  /attach <path>        Queue file(s) for the **next** message (PDF, images, …); /attach list|clear",
      "                        Aliases: /image /img /file",
      "  /trust                Show workspace trust status (file/shell tools)",
      "  /trust on|off         Trust or revoke trust for this project root (~/.gemcode/trust.json)",
      "  /super                Fully autonomous session (auto-approve tools; no HITL). /super off to disable flag only",
      "  /init                 Analyze project structure and generate gemcode.md",
      "  /init force           Regenerate gemcode.md even if it already exists",
      "  /cost                 Show token usage and estimated cost for this session",
      "  /notes                Show agent auto-notes (.gemcode/notes.md)",
      "  /notes clear          Delete all notes",
      "  /notes edit           Open notes in $EDITOR",
      "  /create gemskill <name> [description]  Create a new GemSkill (SKILL.md scaffold)",
      "                        Tip: you can also type “I want to make a new skill” and follow the wizard",
      "  /gemskill <name>        Load an existing GemSkill into this session (system prompt)",
      "  /gemskill list|clear    List skills or unload all session-loaded skills",
      "  /append gemskill <name> <request>  Ask the agent to edit that skill file",
      "  /caveman [level]|off    Terse output mode (like caveman-speak). Levels: lite|full|ultra|wenyan-lite|wenyan|wenyan-ultra",
      "  /style                List available output styles",
      "  /style <name>|off     Activate an output style for this session",
      "  /rules                Show loaded rule files (from .gemcode/rules/)",
      "  /diff                 Show git diff; or `/diff last` / `/diff cp_...` for checkpoint→workspace diff",
      "  /rewind [checkpoint]  List checkpoints or restore one (alias: /checkpoint)",
      "  /add-dir <path>       Add an extra directory for read/search access (safe multi-root)",
      "  /add-dir list         Show added directories",
      "  /add-dir remove <name> Remove an added directory by name",
      "  /batch <goal>         Parallel large-change orchestrator (built-in GemSkill)",
      "",
      "  Session:",
      "  /help                 Show this help",
      "  /status               Full status: model, capabilities, thinking, limits",
      "  /config               All active config fields (model, caps, context, thinking)",
      "  /session              Print current session id and name",
      "  /session list         List all sessions (most recent first)",
      "  /session name <n>     Name the current session",
      "  /session resume <n>   Resume session by name, ID prefix, or full UUID",
      "  /session new          Start a fresh session (history reset)",
      "  /clear                Alias for /session new",
      "  /compact              Force context compaction now (summarize history)",
      "  /compact <focus>      Compact with custom focus, e.g. /compact test output",
      "  /summarise [focus]    Save a durable session summary, persist key facts, then start fresh",
      "  /summarize [focus]    Alias of /summarise",
      "  /review               Parallel code review: security + style + correctness",
      "  /review <path>        Review a specific file or directory",
      "  /context              Show context pressure + last prompt tokens",
      "  /audit [N]            Tail of .gemcode/audit.log (default 40 lines)",
      "  /tools                List tool inventory for this config",
      "  /tools smoke          Declaration compile check only (failures listed)",
      "  /mcp                  MCP status (reads .gemcode/mcp.json; shows loaded toolsets)",
      "  /mcp list             List configured MCP servers",
      "  /mcp reload           Rebuild runner to reload MCP toolsets",
      "  /automations          Local scheduled automations (GemCode Runtime IPC) + heartbeat",
      "  /automations list     List .gemcode/automations/*.json",
      "  /automations run <n>  Enqueue an automation now (needs gemcode runtime IPC)",
      "  /afc                  AFC prompt defaults (avoid afc> prompt)",
      "  /eval [llm]           Run tools_smoke (+ pytest if tests/ exist); optional LLM goldens",
      "  /autotune init <tag>  Git branch autotune/<tag> for experiment tracking",
      "  /autotune eval [llm]  Eval + append .gemcode/evals/autotune_ledger.jsonl",
      "  /curated              Show GEMCODE_MEMORY.md / GEMCODE_USER.md snapshot",
      "  /login                How to run gemcode login (API key outside REPL)",
      "  /live-audio           How to run gemcode live-audio (mic → Gemini Live)",
      "  /doctor               Environment sanity check",
      "  /version              Print GemCode version hint",
      "  /exit                 Exit the REPL",
      "",
      "  Model:",
      "  /model                Show model routing info",
      "  /model use <id>       Override model for this REPL session",
      "  /model list           List available Gemini model IDs",
      "  /mode                 Show current model_mode",
      "  /mode <fast|balanced|quality|auto>  Set model selection strategy",
      "",
      "  Capabilities (all require runner rebuild — shown automatically):",
      "  /computer             Show browser automation status (Playwright Chromium)",
      "  /computer on|off      Enable/disable browser computer use",
      "  /computer url         Show current browser URL",
      "  /research             Show deep-research status",
      "  /research on|off      Enable/disable Google Search + URL Context tools",
      "  /maps on|off          Enable/disable Maps grounding (runner rebuild)",
      "  /embeddings on|off    Enable/disable semantic file search (Embeddings API)",
      "  /caps                 View all capability flags",
      "  /caps <research|embeddings|all|reset>  Bulk capability control",
      "  /memory               Show persistent memory status",
      "  /memory on|off        Enable/disable session memory",
      "",
      "  Thinking:",
      "  /thinking             Show current thinking config",
      "  /thinking verbose     Show full thinking text each turn",
      "  /thinking brief       Show collapsed one-line excerpt",
      "  /thinking off         Disable model thinking",
      "  /thinking on          Re-enable thinking (auto budget/level)",
      "  /thinking budget <N>  Set thinking token budget (Gemini 2.5, 0=off, -1=dynamic)",
      "  /thinking level <L>   Set thinking level: minimal|low|medium|high (Gemini 3.x)",
      "",
      "  Limits:",
      "  /limits               Show all execution limits",
      "  /limits calls <N>     Set max LLM calls per turn",
      "  /budget               Show per-turn token budget",
      "  /budget <N>|off       Set or clear per-turn token budget",
      "",
      "  Other:",
      "  /permissions          Show permission / HITL settings",
      "  /hooks                Show post-turn hook configuration",
      "  /kaira                Background jobs CLI help (prefer: gemcode runtime)",
      "  /agent                Create/manage agent workspaces (new agents)  ·  /agent list|tree|status",
      "  /runtime              Show runtime socket + how to attach/connect",
      "  /bus                  Send/publish lightweight bus messages over IPC",
      "  /inbox                Configure which bus messages this UI shows (filters)",
      "  /code [on|off]        Toggle sandboxed Python executor (ADK BuiltInCodeExecutor)",
      "  /plan [on|off]        Toggle plan mode — agent plans before executing tools",
  ]
