"""Allowlisted subprocess execution."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

from google.adk.tools.tool_context import ToolContext

from gemcode.config import GemCodeConfig
from gemcode.hitl_session import HITL_STICKY_SESSION_KEY
from gemcode.paths import PathEscapeError, resolve_under_root
from gemcode.tools.shell_gate import consume_confirmed_shell_if_matches
from gemcode.trust import is_trusted_root


def _merge_child_env(keys: list[str], values: list[str]) -> dict[str, str]:
  """Merge parallel key/value lists into os.environ (Gemini API rejects dict-typed tool params)."""
  out = {**os.environ}
  if not keys and not values:
    return out
  for k, v in zip(keys, values):
    if not isinstance(k, str) or not isinstance(v, str):
      continue
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
      continue
    if len(v) > 8000:
      continue
    out[k] = v
  return out


def make_run_command(cfg: GemCodeConfig):
  root = cfg.project_root
  trusted = is_trusted_root(root)

  def _emit(msg: dict) -> None:
    em = getattr(cfg, "_ide_emitter", None)
    if em is not None:
      try:
        em.send(msg)
      except Exception:
        pass

  def _run_command_sync(
    command: str,
    args: list[str] | None = None,
    timeout_seconds: int = 120,
    tool_context: ToolContext | None = None,
    cwd_subdir: str = ".",
    background: bool = False,
    extra_env_keys: list[str] | None = None,
    extra_env_values: list[str] | None = None,
  ) -> dict:
    """
    Run an allowlisted executable with arguments.

    Working directory is the project root, or a subdirectory given by `cwd_subdir`
    (relative path, e.g. "my-app" for `npm run dev` inside that folder). Do not use
    `bash` or `cd &&`; set `cwd_subdir` instead.

    For long-running servers (e.g. `npm run dev`), set `background=True` to start
    a detached process and return its PID (non-interactive; no TTY for the child).

    Optional `extra_env_keys` / `extra_env_values` are parallel lists (same length)
    merged into the child environment (e.g. keys ["CI"], values ["1"] for
    non-interactive scaffolding tools). Omit both to use the default environment.
    """
    if getattr(cfg, "ide_proposal_mode", False):
      exe = str(command or "").strip()
      args2 = list(args or [])
      if not getattr(cfg, "ide_allow_shell", False):
        _emit({"type": "permission_request", "kind": "shell", "detail": f"run_command({exe})"})
        return {"error": "shell_not_allowed"}
      _emit(
        {
          "type": "command_suggestion",
          "cmd": " ".join([exe, *args2]).strip(),
          "cwd_subdir": cwd_subdir,
          "background": bool(background),
        }
      )
      return {"suggested": True, "command": [exe, *args2], "cwd_subdir": cwd_subdir, "background": bool(background)}

    if not trusted:
      return {"error": "Project folder is not trusted. Re-run GemCode and approve folder trust."}
    if not (cwd_subdir or "").strip():
      cwd_subdir = "."
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
    sticky_ok = False
    try:
      if tool_context is not None and tool_context.state.get(
          HITL_STICKY_SESSION_KEY
      ):
        sticky_ok = True
    except Exception:
      pass
    user_ok = bool(
        sticky_ok or consume_confirmed_shell_if_matches(exe)
    )
    if not user_ok and exe not in allowed:
      return {
        "error": (
          f"Command {exe!r} not in allowlist. Add it to GEMCODE_ALLOW_COMMANDS "
          f"(comma-separated), or approve the command when GemCode prompts you."
        ),
        "error_kind": "permission_denied",
      }

    resolved = shutil.which(exe)
    if not resolved:
      return {"error": f"Executable not found on PATH: {exe}"}

    try:
      exec_cwd = resolve_under_root(root, cwd_subdir)
    except PathEscapeError as e:
      return {"error": str(e)}
    if not exec_cwd.is_dir():
      return {
          "error": (
              f"cwd_subdir={cwd_subdir!r} is not an existing directory under the project. "
              "Create it first or fix the path."
          )
      }

    ek = list(extra_env_keys or [])
    ev = list(extra_env_values or [])
    if len(ek) != len(ev):
      return {
          "error": (
              "extra_env_keys and extra_env_values must be parallel lists of the same length "
              "(one value per key)."
          ),
      }

    child_env = _merge_child_env(ek, ev)

    if background:
      try:
        proc = subprocess.Popen(
            [resolved, *args],
            cwd=str(exec_cwd),
            env=child_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
      except OSError as e:
        return {"error": f"Failed to start background process: {e}"}
      return {
          "command": [exe, *args],
          "cwd": str(exec_cwd.relative_to(root)) if exec_cwd != root else ".",
          "background": True,
          "pid": proc.pid,
          "note": (
              "Process started in the background. It does not share this terminal. "
              "Stop it with kill from the OS when done."
          ),
      }

    try:
      proc = subprocess.run(
          [resolved, *args],
          cwd=str(exec_cwd),
          capture_output=True,
          text=True,
          timeout=timeout_seconds,
          env=child_env,
          check=False,
      )
      try:
        from gemcode.dynamic_policy import get_dynamic_caps
        caps = get_dynamic_caps(cfg)
        out_cap = caps.run_stdout_chars
        err_cap = caps.run_stderr_chars
      except Exception:
        out_cap = 20_000
        err_cap = 20_000
      return {
          "command": [exe, *args],
          "cwd": str(exec_cwd.relative_to(root)) if exec_cwd != root else ".",
          "exit_code": proc.returncode,
          "stdout": proc.stdout[:out_cap],
          "stderr": proc.stderr[:err_cap],
      }
    except subprocess.TimeoutExpired:
      return {"error": f"Timeout after {timeout_seconds}s"}

  async def run_command(
    command: str,
    args: list[str] | None = None,
    timeout_seconds: int = 120,
    tool_context: ToolContext | None = None,
    cwd_subdir: str = ".",
    background: bool = False,
    extra_env_keys: list[str] | None = None,
    extra_env_values: list[str] | None = None,
  ) -> dict:
    """
    Async wrapper for allowlisted subprocess execution.

    Why: synchronous subprocess calls block the TUI event loop, freezing the live
    spinner timers while tools run. We offload blocking work to a thread.
    """
    if background:
      return _run_command_sync(
        command,
        args=args,
        timeout_seconds=timeout_seconds,
        tool_context=tool_context,
        cwd_subdir=cwd_subdir,
        background=True,
        extra_env_keys=extra_env_keys,
        extra_env_values=extra_env_values,
      )
    return await asyncio.to_thread(
      _run_command_sync,
      command,
      args,
      timeout_seconds,
      tool_context,
      cwd_subdir,
      False,
      extra_env_keys,
      extra_env_values,
    )

  return run_command
