"""
Optional MCP toolsets from `.gemcode/mcp.json`.

Schema (example):
{
  "servers": [
    {
      "name": "docs",
      "stdio": { "command": "npx", "args": ["-y", "@some/mcp-server"] }
    }
  ]
}

Requires: pip install gemcode[mcp]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig


def load_mcp_toolsets(cfg: GemCodeConfig) -> list:
  path = cfg.project_root / ".gemcode" / "mcp.json"
  if not path.is_file():
    return []
  try:
    data = json.loads(path.read_text(encoding="utf-8"))
  except json.JSONDecodeError as e:
    raise ValueError(f"Invalid mcp.json: {e}") from e

  try:
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
    from mcp import StdioServerParameters
  except ImportError as e:
    raise ImportError("Install MCP extras: pip install gemcode[mcp]") from e

  servers = data.get("servers") or []
  toolsets: list[Any] = []
  for s in servers:
    stdio = s.get("stdio") or {}
    cmd = stdio.get("command")
    args = stdio.get("args") or []
    if not cmd:
      continue
    params = StdioServerParameters(command=cmd, args=args)
    prefix = s.get("name") or "mcp"
    toolsets.append(
      McpToolset(
        connection_params=params,
        tool_name_prefix=prefix,
      )
    )
  return toolsets
