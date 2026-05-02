"""
Tool Synthesis — Agent creates new reusable tools at runtime.

When the agent detects it's doing the same multi-step operation repeatedly,
it can synthesize a new tool (a shell script or Python function) that
encapsulates that pattern. Future invocations use the synthesized tool
instead of repeating the steps.

Storage: .gemcode/synthesized_tools/
Each tool is a script with metadata in a companion .json file.

This is the "self-evolving" pattern: the agent extends its own capabilities
by creating tools from experience.
"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig


def _tools_dir(project_root: Path) -> Path:
  return project_root / ".gemcode" / "synthesized_tools"


def enabled() -> bool:
  return os.environ.get("GEMCODE_TOOL_SYNTHESIS", "1").strip().lower() in (
    "1", "true", "yes", "on",
  )


def list_synthesized_tools(project_root: Path) -> list[dict[str, Any]]:
  """List all synthesized tools."""
  d = _tools_dir(project_root)
  if not d.is_dir():
    return []
  tools: list[dict[str, Any]] = []
  for meta_path in sorted(d.glob("*.json")):
    try:
      meta = json.loads(meta_path.read_text(encoding="utf-8"))
      tools.append(meta)
    except Exception:
      continue
  return tools


def get_synthesized_tool(project_root: Path, name: str) -> dict[str, Any] | None:
  """Get a specific synthesized tool by name."""
  meta_path = _tools_dir(project_root) / f"{name}.json"
  if not meta_path.is_file():
    return None
  try:
    return json.loads(meta_path.read_text(encoding="utf-8"))
  except Exception:
    return None


def create_synthesized_tool(
  project_root: Path,
  *,
  name: str,
  description: str,
  script_content: str,
  script_type: str = "bash",  # bash or python
  args: list[str] | None = None,
) -> dict[str, Any]:
  """
  Create a new synthesized tool.

  The tool is stored as a script file + metadata JSON.
  It can be invoked via the `run_synthesized_tool` function.
  """
  import re

  nm = (name or "").strip().lower()
  if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", nm):
    return {"ok": False, "error": "invalid name (lowercase, start with letter, max 64 chars)"}
  if not description.strip():
    return {"ok": False, "error": "description is required"}
  if not script_content.strip():
    return {"ok": False, "error": "script_content is required"}

  d = _tools_dir(project_root)
  d.mkdir(parents=True, exist_ok=True)

  ext = ".sh" if script_type == "bash" else ".py"
  script_path = d / f"{nm}{ext}"
  meta_path = d / f"{nm}.json"

  # Write script
  script_path.write_text(script_content, encoding="utf-8")
  # Make executable
  script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

  # Write metadata
  meta = {
    "name": nm,
    "description": description.strip(),
    "script_type": script_type,
    "script_path": str(script_path),
    "args": args or [],
    "created_ms": int(time.time() * 1000),
    "run_count": 0,
    "last_run_ms": 0,
  }
  meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

  return {"ok": True, "name": nm, "path": str(script_path)}


def run_synthesized_tool(
  project_root: Path,
  name: str,
  args: list[str] | None = None,
  timeout: int = 300,
) -> dict[str, Any]:
  """Run a synthesized tool and return its output."""
  import subprocess

  meta = get_synthesized_tool(project_root, name)
  if meta is None:
    return {"error": f"tool not found: {name}"}

  script_path = Path(meta["script_path"])
  if not script_path.is_file():
    return {"error": f"script missing: {script_path}"}

  script_type = meta.get("script_type", "bash")
  cmd: list[str]
  if script_type == "python":
    cmd = ["python3", str(script_path)] + (args or [])
  else:
    cmd = ["bash", str(script_path)] + (args or [])

  try:
    result = subprocess.run(
      cmd,
      capture_output=True,
      text=True,
      timeout=timeout,
      cwd=str(project_root),
    )

    # Update run count
    meta_path = _tools_dir(project_root) / f"{name}.json"
    meta["run_count"] = meta.get("run_count", 0) + 1
    meta["last_run_ms"] = int(time.time() * 1000)
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
      "ok": result.returncode == 0,
      "exit_code": result.returncode,
      "stdout": result.stdout[:10000],
      "stderr": result.stderr[:5000],
    }
  except subprocess.TimeoutExpired:
    return {"error": f"timeout after {timeout}s"}
  except Exception as e:
    return {"error": f"{type(e).__name__}: {e}"}


def make_tool_synthesis_tools(cfg: GemCodeConfig) -> list:
  """Build tools for creating and running synthesized tools."""

  def synthesize_tool(
    name: str,
    description: str,
    script_content: str,
    script_type: str = "bash",
  ) -> dict:
    """
    Create a new reusable tool from a script.

    Use this when you find yourself repeating the same multi-step operation.
    Instead of running the same commands every time, synthesize a tool once
    and invoke it by name in the future.

    The tool is stored in .gemcode/synthesized_tools/ and persists across sessions.

    Args:
      name: Tool name (lowercase, letters/numbers/dashes, e.g., "run-tests", "deploy-staging")
      description: What this tool does (shown in tool listings)
      script_content: The bash or python script content
      script_type: "bash" or "python" (default: bash)

    Examples:
      synthesize_tool("run-tests", "Run pytest with coverage", "pytest --cov=src -q")
      synthesize_tool("deploy-staging", "Deploy to staging", "git push origin main && ssh staging 'cd app && git pull'")
      synthesize_tool("check-types", "Run mypy type checking", "mypy src/ --ignore-missing-imports")
    """
    if not enabled():
      return {"ok": False, "error": "tool synthesis disabled (GEMCODE_TOOL_SYNTHESIS=0)"}
    return create_synthesized_tool(
      cfg.project_root,
      name=name,
      description=description,
      script_content=script_content,
      script_type=script_type,
    )

  def run_tool(name: str, args: str = "") -> dict:
    """
    Run a previously synthesized tool by name.

    Args:
      name: The tool name (as created with synthesize_tool)
      args: Optional space-separated arguments to pass to the script
    """
    arg_list = args.split() if args.strip() else []
    return run_synthesized_tool(cfg.project_root, name, args=arg_list)

  def list_tools() -> dict:
    """List all synthesized tools available in this project."""
    tools = list_synthesized_tools(cfg.project_root)
    return {
      "ok": True,
      "tools": [
        {"name": t["name"], "description": t["description"], "run_count": t.get("run_count", 0)}
        for t in tools
      ],
      "count": len(tools),
    }

  synthesize_tool.__name__ = "synthesize_tool"
  run_tool.__name__ = "run_synthesized_tool"
  list_tools.__name__ = "list_synthesized_tools"

  return [synthesize_tool, run_tool, list_tools]
