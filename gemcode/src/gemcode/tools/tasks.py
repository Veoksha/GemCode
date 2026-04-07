"""
Background task management — analogous to Reference UI TaskStop + TaskOutput.

When bash(command, background=True) spawns a process, GemCode registers it in
a module-level registry keyed by PID. The tools here let the agent:

  list_tasks()          — see all background tasks (running or finished)
  kill_task(pid)        — stop a background task by PID
  task_output(pid)      — read stdout/stderr captured from a background task

Background output capture:
  bash(..., background=True) now writes stdout+stderr to a temp file when
  capture=True (the default). task_output() reads that file. Processes started
  before this feature was added, or with capture=False, will show "no output".

Usage pattern (mirrors reference terminal UI):
  pid_info = bash("npm run dev", background=True)   # starts dev server
  task_output(pid_info["pid"])                       # check its startup log
  kill_task(pid_info["pid"])                         # stop it when done
"""

from __future__ import annotations

import os
import signal
import tempfile
import time
from pathlib import Path
from typing import Any

# Module-level registry: pid → task metadata
_TASK_REGISTRY: dict[int, dict[str, Any]] = {}


def register_task(
    pid: int,
    *,
    command: str,
    cwd: str,
    log_path: str | None = None,
) -> None:
    """Called by bash() when starting a background process."""
    _TASK_REGISTRY[pid] = {
        "pid": pid,
        "command": command,
        "cwd": cwd,
        "log_path": log_path,
        "started_at": time.strftime("%H:%M:%S"),
    }


def _is_running(pid: int) -> bool:
    """Return True if the process is still alive."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists but we can't signal it


def make_task_tools(cfg: GemCodeConfig):  # noqa: F821 (forward ref ok at runtime)
    def list_tasks() -> dict[str, Any]:
        """
        List all background tasks started in this session via bash(..., background=True).

        Returns each task's PID, command, working directory, start time, and
        current status (running / finished).

        Use this to check whether a background server is still alive, find the
        PID to read its output (task_output), or decide which tasks to stop.
        """
        tasks = []
        for pid, info in list(_TASK_REGISTRY.items()):
            running = _is_running(pid)
            tasks.append({
                "pid": pid,
                "command": info.get("command", ""),
                "cwd": info.get("cwd", ""),
                "started_at": info.get("started_at", ""),
                "status": "running" if running else "finished",
                "log_path": info.get("log_path"),
            })
        # Sort running tasks first, then by pid
        tasks.sort(key=lambda t: (0 if t["status"] == "running" else 1, t["pid"]))
        return {
            "tasks": tasks,
            "count": len(tasks),
            "running": sum(1 for t in tasks if t["status"] == "running"),
        }

    def kill_task(pid: int, force: bool = False) -> dict[str, Any]:
        """
        Stop a background task by PID.

        Sends SIGTERM by default (graceful shutdown). Use force=True to send
        SIGKILL (immediate termination, like kill -9).

        Args:
            pid: Process ID from bash(..., background=True) or list_tasks().
            force: If True, send SIGKILL instead of SIGTERM (default False).
        """
        if not isinstance(pid, int) or pid <= 0:
            return {"error": "pid must be a positive integer"}

        if not _is_running(pid):
            # Remove from registry
            _TASK_REGISTRY.pop(pid, None)
            return {"ok": True, "pid": pid, "note": "Process was already finished"}

        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            _TASK_REGISTRY.pop(pid, None)
            return {"ok": True, "pid": pid, "note": "Process already exited"}
        except PermissionError as e:
            return {"error": f"Permission denied: {e}", "pid": pid}

        # Give it a moment to die after SIGTERM
        if not force:
            import time as _time
            _time.sleep(0.5)
            if _is_running(pid):
                return {
                    "ok": True,
                    "pid": pid,
                    "note": "SIGTERM sent — process may still be shutting down. Use kill_task(pid, force=True) to force.",
                }

        _TASK_REGISTRY.pop(pid, None)
        sig_name = "SIGKILL" if force else "SIGTERM"
        return {"ok": True, "pid": pid, "signal": sig_name, "note": f"{sig_name} sent to process {pid}"}

    def task_output(pid: int, max_chars: int = 20_000, tail: bool = True) -> dict[str, Any]:
        """
        Read captured stdout/stderr from a background task.

        Background tasks started with bash(..., background=True) write their
        output to a temp log file. This tool reads that file.

        Args:
            pid: Process ID from bash(..., background=True) or list_tasks().
            max_chars: Maximum characters to return (default 20 000).
            tail: If True (default), return the LAST max_chars (most recent output).
                  If False, return from the beginning.
        """
        if not isinstance(pid, int) or pid <= 0:
            return {"error": "pid must be a positive integer"}

        info = _TASK_REGISTRY.get(pid)
        if info is None:
            return {
                "error": (
                    f"No record of PID {pid} in this session. "
                    "Only tasks started with bash(..., background=True) in the current session are tracked."
                ),
                "pid": pid,
            }

        log_path = info.get("log_path")
        if not log_path:
            return {
                "pid": pid,
                "status": "running" if _is_running(pid) else "finished",
                "output": None,
                "note": "Output capture not available for this task (started without log capture).",
            }

        log = Path(log_path)
        if not log.exists():
            return {
                "pid": pid,
                "status": "running" if _is_running(pid) else "finished",
                "output": "",
                "note": "Log file not found — process may not have written anything yet.",
            }

        try:
            content = log.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return {"error": f"Cannot read log: {e}", "pid": pid}

        total_chars = len(content)
        if tail and len(content) > max_chars:
            content = "...[truncated, showing tail]...\n" + content[-max_chars:]
            truncated = True
        elif not tail and len(content) > max_chars:
            content = content[:max_chars] + "\n...[truncated]..."
            truncated = True
        else:
            truncated = False

        return {
            "pid": pid,
            "command": info.get("command", ""),
            "status": "running" if _is_running(pid) else "finished",
            "output": content,
            "total_chars": total_chars,
            "truncated": truncated,
        }

    return list_tasks, kill_task, task_output


def make_log_file_for_task() -> str:
    """Create a temp log file for capturing background task output. Returns the path."""
    fd, path = tempfile.mkstemp(prefix="gemcode_task_", suffix=".log")
    os.close(fd)
    return path
