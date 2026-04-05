"""
Built-in REPL slash-command handlers (Claude Code–style thin wrappers).

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
  ac = cfg.allow_commands
  if ac is None:
    preview = "(default allowlist)"
  else:
    names = sorted(ac)
    preview = ", ".join(names[:16])
    if len(names) > 16:
      preview += f", … (+{len(names) - 16} more)"
  return [
      "Permissions:",
      f"  permission_mode: {cfg.permission_mode}",
      f"  yes_to_all: {cfg.yes_to_all}",
      f"  interactive_permission_ask: {getattr(cfg, 'interactive_permission_ask', False)}",
      f"  interactive_hitl_sticky_session: {getattr(cfg, 'interactive_hitl_sticky_session', True)}",
      f"  allow_commands: {preview}",
  ]


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
  default_hook = cfg.project_root / ".gemcode" / "hooks" / "post_turn"
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


def slash_help_lines() -> list[str]:
  return [
      "Slash commands:",
      "  (CLI) gemcode -C DIR  Use a project folder as root (recommended vs. ~ )",
      "  (CLI) gemcode login   Save or change API key (~/.gemcode/credentials.json)",
      "  /help                 Show this help",
      "  /status               Show current session/model info",
      "  /config               Show key GemCode env/config toggles",
      "  /session              Print current session id",
      "  /session new          Start a new session id (history reset)",
      "  /clear                Alias for /session new",
      "  /compact              Force autocompact now (summarize history)",
      "  /context              Show context pressure + last prompt tokens",
      "  /audit [N]            Tail of .gemcode/audit.log (default 40 lines)",
      "  /tools                List tool inventory for this config",
      "  /doctor               Environment sanity check",
      "  /model                Show model routing info",
      "  /model use <id>       Override model for this REPL session",
      "  /model list            List available Gemini model IDs",
      "  /thinking             Show current thinking config",
      "  /thinking verbose     Show full thinking text each turn",
      "  /thinking brief       Show collapsed one-line excerpt (default)",
      "  /thinking off         Disable model thinking",
      "  /thinking on          Re-enable thinking (auto budget/level)",
      "  /thinking budget <N>  Set thinking token budget (Gemini 2.5, 0=off, -1=dynamic)",
      "  /thinking level <L>   Set thinking level: minimal|low|medium|high (Gemini 3.x)",
      "  /permissions          Show permission / HITL settings",
      "  /memory               Show persistent memory settings",
      "  /hooks                Show post-turn hook configuration",
      "  /version              Print GemCode version hint",
      "  /exit                 Exit the REPL",
  ]
