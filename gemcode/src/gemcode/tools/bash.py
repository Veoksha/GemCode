"""
Arbitrary shell execution via bash -c.

Analogous to OpenClaude's BashTool — allows pipelines, redirects, multi-step
shell workflows that run_command (basename-only) cannot express.

Security model: same gating as run_command (SHELL_TOOLS category), requires
--yes / interactive approval. Timeout-bounded; no TTY for child processes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from gemcode.config import GemCodeConfig
from gemcode.paths import PathEscapeError, resolve_under_root
from gemcode.trust import is_trusted_root


def make_bash_tool(cfg: GemCodeConfig):
    root = cfg.project_root
    trusted = is_trusted_root(root)

    def bash(
        command: str,
        timeout_seconds: int = 120,
        cwd_subdir: str = ".",
        background: bool = False,
    ) -> dict:
        """
        Run an arbitrary shell command via bash. Supports pipelines, redirects,
        subshells, and multi-step workflows that run_command cannot express.

        Use this for:
        - Git operations: bash("git log --oneline -20")
        - Pipelines: bash("cat package.json | python3 -m json.tool")
        - Finding files: bash("find . -name '*.py' -newer setup.py | head -20")
        - Complex builds: bash("cd frontend && npm ci && npm run build")
        - Inspecting output: bash("ls -la | grep '.py'")
        - Running tests with flags: bash("pytest -x -q --tb=short 2>&1 | head -100")

        For long-running servers use background=True. cwd_subdir sets working
        directory relative to the project root.

        IMPORTANT: This runs real shell commands. Be precise and avoid destructive
        operations (rm -rf, force-push, etc.) without explicit user approval.
        """
        if not trusted:
            return {"error": "Project folder is not trusted. Re-run GemCode and approve folder trust."}

        if not command or not command.strip():
            return {"error": "command must not be empty"}

        if timeout_seconds < 1:
            timeout_seconds = 1
        if timeout_seconds > 600:
            timeout_seconds = 600

        bash_exe = shutil.which("bash")
        if not bash_exe:
            return {"error": "bash not found on PATH"}

        if not (cwd_subdir or "").strip():
            cwd_subdir = "."
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

        env = {**os.environ}

        if background:
            try:
                proc = subprocess.Popen(
                    [bash_exe, "-c", command],
                    cwd=str(exec_cwd),
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError as e:
                return {"error": f"Failed to start background process: {e}"}
            return {
                "command": command,
                "cwd": str(exec_cwd.relative_to(root)) if exec_cwd != root else ".",
                "background": True,
                "pid": proc.pid,
                "note": "Process started in the background. Stop it with kill from the OS when done.",
            }

        try:
            proc = subprocess.run(
                [bash_exe, "-c", command],
                cwd=str(exec_cwd),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=env,
                check=False,
            )
            stdout = proc.stdout[:80_000]
            stderr = proc.stderr[:20_000]
            result: dict = {
                "command": command,
                "cwd": str(exec_cwd.relative_to(root)) if exec_cwd != root else ".",
                "exit_code": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
            }
            if proc.returncode != 0 and not stdout and not stderr:
                result["note"] = f"Command exited {proc.returncode} with no output."
            return result
        except subprocess.TimeoutExpired:
            return {"error": f"Timeout after {timeout_seconds}s", "command": command}

    return bash
