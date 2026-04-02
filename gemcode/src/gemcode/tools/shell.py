"""Allowlisted subprocess execution."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from gemcode.config import GemCodeConfig
from gemcode.trust import is_trusted_root


def make_run_command(cfg: GemCodeConfig):
  root = cfg.project_root
  trusted = is_trusted_root(root)

  def run_command(
    command: str,
    args: list[str] | None = None,
    timeout_seconds: int = 120,
  ) -> dict:
    """
    Run an allowlisted executable with arguments under the project root cwd.

    The executable must be a basename (no shell metacharacters) and appear in
    GEMCODE_ALLOW_COMMANDS / default allowlist.
    """
    if not trusted:
      return {"error": "Project folder is not trusted. Re-run GemCode and approve folder trust."}
    args = args or []
    if timeout_seconds < 1:
      timeout_seconds = 1
    if timeout_seconds > 600:
      timeout_seconds = 600
    if any(c in command for c in ";|&$`"):
      return {"error": "Command must be a single executable name, not a shell snippet"}
    exe = Path(command).name
    if exe != command:
      return {"error": "Use basename only for command (e.g. pytest, not /usr/bin/pytest)"}

    allowed = cfg.allow_commands
    if exe not in allowed:
      return {
        "error": (
          f"Command {exe!r} not in allowlist. Add it to GEMCODE_ALLOW_COMMANDS "
          f"(comma-separated)."
        )
      }

    resolved = shutil.which(exe)
    if not resolved:
      return {"error": f"Executable not found on PATH: {exe}"}
    try:
      proc = subprocess.run(
        [resolved, *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env={**os.environ},
        check=False,
      )
      return {
        "command": [exe, *args],
        "exit_code": proc.returncode,
        "stdout": proc.stdout[:50_000],
        "stderr": proc.stderr[:50_000],
      }
    except subprocess.TimeoutExpired:
      return {"error": f"Timeout after {timeout_seconds}s"}

  return run_command
