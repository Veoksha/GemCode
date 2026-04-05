"""
Arbitrary shell execution via bash -c.

Analogous to OpenClaude's BashTool — allows pipelines, redirects, multi-step
shell workflows that run_command (basename-only) cannot express.

Provides two variants:
  bash()          — standard blocking call, returns full output dict
  bash_stream()   — ADK streaming tool, yields stdout line-by-line in real-time
                    (requires ADK @streaming_tool support in the runner)

Security model: same gating as run_command (SHELL_TOOLS category), requires
--yes / interactive approval. Timeout-bounded; no TTY for child processes.
"""

from __future__ import annotations

import asyncio
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


def make_bash_stream_tool(cfg: GemCodeConfig):
  """
  Build bash_stream — a streaming variant of bash that yields stdout line-by-line.

  Requires ADK @streaming_tool support (ADK >= 1.0). Falls back to a regular
  async generator that the model sees as incremental updates.

  The model receives individual lines as they arrive from the subprocess,
  enabling real-time visibility into long-running commands like test runners,
  build tools, log tails, and install scripts.
  """
  root = cfg.project_root
  trusted = is_trusted_root(root)

  try:
    from google.adk.tools import streaming_tool as _streaming_tool_decorator
    _has_streaming = True
  except ImportError:
    _has_streaming = False

  async def bash_stream(
      command: str,
      timeout_seconds: int = 300,
      cwd_subdir: str = ".",
      max_lines: int = 500,
  ):
    """
    Run a shell command and stream stdout line-by-line in real-time.

    Unlike bash() which returns all output at once, bash_stream() yields each
    output line as it arrives — ideal for long-running commands where you want
    to see progress:
    - Test runners: bash_stream("pytest -v tests/")
    - Build tools: bash_stream("npm run build")
    - Log tails: bash_stream("tail -f logs/app.log", timeout_seconds=60)
    - Install scripts: bash_stream("pip install -r requirements.txt")

    Args:
        command: Shell command (passed to bash -c)
        timeout_seconds: Maximum run time (default 300s)
        cwd_subdir: Working directory relative to project root
        max_lines: Stop after this many lines (default 500)

    Yields dicts with keys: line, line_no, source ("stdout"/"stderr"), done, exit_code
    """
    if not trusted:
      yield {"error": "Project folder is not trusted.", "done": True}
      return
    if not command or not command.strip():
      yield {"error": "command must not be empty", "done": True}
      return

    bash_exe = shutil.which("bash")
    if not bash_exe:
      yield {"error": "bash not found on PATH", "done": True}
      return

    if not (cwd_subdir or "").strip():
      cwd_subdir = "."
    try:
      exec_cwd = resolve_under_root(root, cwd_subdir)
    except PathEscapeError as e:
      yield {"error": str(e), "done": True}
      return

    if timeout_seconds < 1:
      timeout_seconds = 1
    if timeout_seconds > 600:
      timeout_seconds = 600

    env = {**os.environ}

    try:
      proc = await asyncio.create_subprocess_exec(
          bash_exe, "-c", command,
          stdout=asyncio.subprocess.PIPE,
          stderr=asyncio.subprocess.STDOUT,  # merge stderr into stdout
          cwd=str(exec_cwd),
          env=env,
      )
    except OSError as e:
      yield {"error": f"Failed to start process: {e}", "done": True}
      return

    line_no = 0
    try:
      while True:
        try:
          line_bytes = await asyncio.wait_for(
              proc.stdout.readline(),  # type: ignore[union-attr]
              timeout=min(timeout_seconds, 10),
          )
        except asyncio.TimeoutError:
          # Keep looping until wall-clock timeout
          if proc.returncode is not None:
            break
          continue

        if not line_bytes:
          # EOF
          break

        line_no += 1
        line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
        yield {"line": line, "line_no": line_no, "source": "stdout"}

        if line_no >= max_lines:
          yield {"note": f"Truncated at {max_lines} lines", "done": False}
          proc.kill()
          break

    except Exception as exc:
      yield {"error": str(exc), "done": True}
      return
    finally:
      try:
        await asyncio.wait_for(proc.wait(), timeout=5)
      except asyncio.TimeoutError:
        proc.kill()

    exit_code = proc.returncode or 0
    yield {
        "done": True,
        "exit_code": exit_code,
        "total_lines": line_no,
        "command": command,
        "success": exit_code == 0,
    }

  # Apply ADK streaming_tool decorator if available — enables real-time TUI display
  if _has_streaming:
    try:
      from google.adk.tools import streaming_tool as _dec
      bash_stream = _dec(bash_stream)  # type: ignore[assignment]
    except Exception:
      pass

  return bash_stream
