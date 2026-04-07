"""
Permission rules engine — inspired by allow/deny pattern system.

Users define rules in `.gemcode/settings.json` (project) or `~/.gemcode/settings.json` (global).
Rules are evaluated for every tool call: deny first, then allow, then default.

Schema:
{
  "permissions": {
    "allow": [
      "bash(git *)",
      "bash(npm run *)",
      "bash(pytest *)",
      "read_file(*)",
      "write_file(src/**)"
    ],
    "deny": [
      "bash(rm -rf *)",
      "bash(curl *)",
      "bash(wget *)",
      "read_file(.env)",
      "read_file(.env.*)",
      "write_file(secrets/**)"
    ]
  }
}

Pattern syntax (same as permission rules):
  - "bash"                   — matches ALL bash calls
  - "bash(*)"                — same as above
  - "bash(git *)"            — bash calls whose command starts with "git "
  - "write_file(src/**)"     — write_file calls where path matches src/**
  - "read_file(.env)"        — exact path match for .env
  - "*"                      — matches all tool calls

Evaluation order: deny → allow → default (ask/yes_to_all/strict from cfg)
First matching rule wins.

Files (merged in order — later rules take precedence):
  1. ~/.gemcode/settings.json     (user-global)
  2. .gemcode/settings.json       (project-specific, can override global)

Reloaded on every tool call (file is stat-cached to avoid repeated disk reads).
"""

from __future__ import annotations

# Compatibility shim: older code/tests import make_before_tool_callback from here.
from gemcode.callbacks import make_before_tool_callback  # noqa: F401

import fnmatch
import json
import os
import time
from pathlib import Path
from typing import Any

# ── Cache so we don't re-read files every millisecond ────────────────────────
_file_cache: dict[str, tuple[float, float, list]] = {}  # path → (mtime, stat_time, rules)
_CACHE_TTL = 2.0  # seconds


def _read_settings_rules(path: Path) -> list[dict]:
  """Read and cache permission rules from a settings file."""
  path_str = str(path)
  now = time.monotonic()
  if path_str in _file_cache:
    cached_mtime, cached_time, cached_rules = _file_cache[path_str]
    if now - cached_time < _CACHE_TTL:
      return cached_rules

  if not path.is_file():
    _file_cache[path_str] = (0.0, now, [])
    return []

  try:
    mtime = path.stat().st_mtime
    if path_str in _file_cache and _file_cache[path_str][0] == mtime:
      # File hasn't changed — just refresh the TTL
      old = _file_cache[path_str]
      _file_cache[path_str] = (old[0], now, old[2])
      return old[2]

    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    perms = data.get("permissions") or {}
    rules: list[dict] = []
    for pattern in perms.get("deny") or []:
      rules.append({"action": "deny", "pattern": str(pattern)})
    for pattern in perms.get("allow") or []:
      rules.append({"action": "allow", "pattern": str(pattern)})
    _file_cache[path_str] = (mtime, now, rules)
    return rules
  except Exception:
    _file_cache[path_str] = (0.0, now, [])
    return []


def load_rules(project_root: Path) -> list[dict]:
  """
  Load merged rules from user-global and project settings.
  Order: global first (lower priority), project second (higher priority / overrides).
  """
  global_rules = _read_settings_rules(Path.home() / ".gemcode" / "settings.json")
  project_rules = _read_settings_rules(project_root / ".gemcode" / "settings.json")
  # Deny rules always evaluated first regardless of file order, so combine and re-sort
  all_rules = global_rules + project_rules
  # Stable sort: deny rules before allow rules (deny has priority)
  deny_rules = [r for r in all_rules if r["action"] == "deny"]
  allow_rules = [r for r in all_rules if r["action"] == "allow"]
  return deny_rules + allow_rules


def _parse_pattern(pattern: str) -> tuple[str, str | None]:
  """
  Parse a rule pattern into (tool_glob, arg_glob | None).

  Examples:
    "bash"            → ("bash", None)
    "bash(*)"         → ("bash", "*")
    "bash(git *)"     → ("bash", "git *")
    "write_file(src/*)→ ("write_file", "src/*")
    "*"               → ("*", None)
  """
  pattern = pattern.strip()
  paren_open = pattern.find("(")
  if paren_open == -1:
    return (pattern, None)
  tool_glob = pattern[:paren_open].strip()
  arg_part = pattern[paren_open + 1:]
  if arg_part.endswith(")"):
    arg_part = arg_part[:-1]
  return (tool_glob, arg_part.strip() or None)


def _get_primary_arg(tool_name: str, args: dict[str, Any]) -> str:
  """
  Extract the primary argument to match against the pattern's arg_glob.
  For bash: the command string.
  For file tools: the path argument.
  For everything else: concatenate all string args.
  """
  if tool_name in ("bash", "run_command"):
    return (
        args.get("command") or
        args.get("cmd") or
        args.get("args", [""])[0] if isinstance(args.get("args"), list) else args.get("args", "")
    ) or ""
  # File tools — use first path-like arg
  for key in ("path", "file_path", "file", "filename", "dest", "src"):
    val = args.get(key)
    if val and isinstance(val, str):
      return val
  # Fallback: join all string values
  return " ".join(str(v) for v in args.values() if isinstance(v, str))


def check_rules(
    tool_name: str,
    args: dict[str, Any],
    project_root: Path,
) -> str | None:
  """
  Evaluate permission rules for a tool call.

  Returns:
    "allow"  — explicit allow rule matched → skip normal permission prompts
    "deny"   — explicit deny rule matched  → block the tool call
    None     — no rule matched             → use default behavior (cfg.permission_mode)
  """
  rules = load_rules(project_root)
  if not rules:
    return None

  primary_arg = _get_primary_arg(tool_name, args)

  for rule in rules:
    tool_glob, arg_glob = _parse_pattern(rule["pattern"])

    # Match tool name
    if tool_glob != "*" and not fnmatch.fnmatch(tool_name, tool_glob):
      continue

    # Match argument (if pattern specifies one)
    if arg_glob is not None and arg_glob != "*":
      if not fnmatch.fnmatch(primary_arg, arg_glob):
        continue

    return rule["action"]  # "allow" or "deny"

  return None  # no rule matched


def describe_rules(project_root: Path) -> list[str]:
  """Human-readable rule listing for /permissions output."""
  rules = load_rules(project_root)
  if not rules:
    return ["  (no rules — using default permission_mode)"]
  lines: list[str] = []
  deny_rules = [r for r in rules if r["action"] == "deny"]
  allow_rules = [r for r in rules if r["action"] == "allow"]
  if deny_rules:
    lines.append("  deny:")
    for r in deny_rules:
      lines.append(f"    ✗  {r['pattern']}")
  if allow_rules:
    lines.append("  allow:")
    for r in allow_rules:
      lines.append(f"    ✓  {r['pattern']}")
  return lines
