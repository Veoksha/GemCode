"""
GemCode shell hooks — scriptable lifecycle events inspired by reference terminal UI.

Hooks are shell scripts stored under ``<project_root>/.gemcode/hooks/``.
They receive a JSON payload on stdin and can:
  - pre_tool_use.sh:  return non-zero exit to DENY the tool call, or
                      print JSON {"decision":"deny","reason":"..."} to stderr.
  - post_tool_use.sh: informational; return value ignored.
  - session_start.sh: runs when a new GemCode session starts.
  - session_stop.sh:  runs when the session ends.

HOOK ENVIRONMENT
  All hooks receive these env vars:
    GEMCODE_HOOK_TYPE       — "pre_tool_use" | "post_tool_use" | "session_start" | "session_stop"
    GEMCODE_PROJECT_ROOT    — absolute path to the project root
    GEMCODE_MODEL           — active model id

HOOK STDIN (pre_tool_use, post_tool_use)
  JSON object with:
    { "tool": "<tool_name>",
      "args": { ...tool_args... },
      "type": "pre_tool_use" | "post_tool_use",
      "result": { ...tool_result... }   // post only
    }

PRE_TOOL_USE DECISION
  If exit code is non-zero → the tool is DENIED.
  If exit code is 0 → the tool proceeds normally.
  If stdout starts with '{' and contains "decision": "deny" → the tool is denied.

EXAMPLE HOOKS
  # .gemcode/hooks/pre_tool_use.sh
  #!/bin/bash
  # Deny all delete_file calls
  HOOK=$(cat)
  TOOL=$(echo "$HOOK" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['tool'])")
  if [ "$TOOL" = "delete_file" ]; then
    echo "delete_file blocked by hook" >&2
    exit 1
  fi

  # .gemcode/hooks/session_start.sh
  #!/bin/bash
  echo "[gemcode hook] session started in $GEMCODE_PROJECT_ROOT" >> /tmp/gemcode-audit.log
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_HOOKS_DIR = ".gemcode/hooks"
_TIMEOUT_PRE = 5.0   # seconds — pre_tool_use must respond quickly
_TIMEOUT_POST = 10.0  # seconds — post_tool_use has more budget


def _find_hook(project_root: Path, name: str) -> Path | None:
    """Return the hook file path if it exists and is executable, else None."""
    hooks_dir = project_root / _HOOKS_DIR
    for ext in ("", ".sh", ".py", ".bash"):
        candidate = hooks_dir / f"{name}{ext}"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _run_hook(
    hook_path: Path,
    project_root: Path,
    model: str,
    hook_type: str,
    stdin_data: dict | None = None,
    timeout: float = 5.0,
) -> tuple[int, str, str]:
    """
    Run a hook script.

    Returns (exit_code, stdout, stderr).
    Never raises — all exceptions are caught and logged.
    """
    try:
        env = {
            **os.environ,
            "GEMCODE_HOOK_TYPE": hook_type,
            "GEMCODE_PROJECT_ROOT": str(project_root),
            "GEMCODE_MODEL": model or "",
        }
        stdin_bytes = (
            json.dumps(stdin_data, ensure_ascii=False).encode() if stdin_data else b""
        )
        result = subprocess.run(
            [str(hook_path)],
            input=stdin_bytes,
            capture_output=True,
            timeout=timeout,
            env=env,
            cwd=str(project_root),
        )
        return result.returncode, result.stdout.decode(errors="replace"), result.stderr.decode(errors="replace")
    except subprocess.TimeoutExpired:
        log.warning("[hooks] %s timed out after %.1fs", hook_path.name, timeout)
        return 1, "", f"hook timed out after {timeout}s"
    except Exception as exc:
        log.debug("[hooks] failed to run %s: %s", hook_path.name, exc)
        return 1, "", str(exc)


def run_pre_tool_use_hook(
    project_root: Path,
    model: str,
    tool_name: str,
    args: dict[str, Any],
) -> dict | None:
    """
    Run the pre_tool_use hook (if it exists).

    Returns:
        None  → tool is allowed (default)
        dict  → {"error": "...", "error_kind": "hook_denied"} to deny the tool
    """
    hook = _find_hook(project_root, "pre_tool_use")
    if hook is None:
        return None

    payload = {"tool": tool_name, "args": args, "type": "pre_tool_use"}
    rc, stdout, stderr = _run_hook(
        hook, project_root, model, "pre_tool_use", payload, timeout=_TIMEOUT_PRE
    )

    if rc != 0:
        reason = stderr.strip() or stdout.strip() or f"hook exited {rc}"
        log.info("[hooks] pre_tool_use denied %s: %s", tool_name, reason[:200])
        return {
            "error": f"Tool denied by pre_tool_use hook: {reason[:400]}",
            "error_kind": "hook_denied",
        }

    # Check if stdout contains an explicit deny decision.
    if stdout.strip().startswith("{"):
        try:
            decision = json.loads(stdout.strip())
            if str(decision.get("decision", "")).lower() == "deny":
                reason = decision.get("reason", "hook denied")
                log.info("[hooks] pre_tool_use JSON-denied %s: %s", tool_name, reason)
                return {
                    "error": f"Tool denied by hook: {reason}",
                    "error_kind": "hook_denied",
                }
        except Exception:
            pass

    return None  # allowed


def run_post_tool_use_hook(
    project_root: Path,
    model: str,
    tool_name: str,
    args: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Run the post_tool_use hook if it exists. Return value is ignored."""
    hook = _find_hook(project_root, "post_tool_use")
    if hook is None:
        return
    payload = {
        "tool": tool_name,
        "args": args,
        "result": result,
        "type": "post_tool_use",
    }
    _run_hook(hook, project_root, model, "post_tool_use", payload, timeout=_TIMEOUT_POST)


def run_session_start_hook(project_root: Path, model: str) -> None:
    """Run the session_start hook if it exists."""
    hook = _find_hook(project_root, "session_start")
    if hook is None:
        return
    _run_hook(hook, project_root, model, "session_start", timeout=10.0)


def run_session_stop_hook(project_root: Path, model: str) -> None:
    """Run the session_stop hook if it exists."""
    hook = _find_hook(project_root, "session_stop")
    if hook is None:
        return
    _run_hook(hook, project_root, model, "session_stop", timeout=10.0)
