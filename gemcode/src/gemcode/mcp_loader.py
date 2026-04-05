"""
Optional MCP toolsets from `.gemcode/mcp.json`.

Now supports all three ADK connection types — like Claude Code's MCP integration:

Schema (example):
{
  "servers": [
    {
      "name": "filesystem",
      "stdio": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"] }
    },
    {
      "name": "github",
      "http": {
        "url": "https://api.githubcopilot.com/mcp/",
        "headers": { "Authorization": "Bearer ${GITHUB_TOKEN}" }
      }
    },
    {
      "name": "notion",
      "sse": { "url": "https://mcp.notion.com/sse" }
    }
  ]
}

Connection types:
  stdio  — local subprocess (npx, python, etc.) — legacy, always worked
  http   — Streamable HTTP (remote servers, Cloud Run, Smithery.ai)
  sse    — Server-Sent Events (older remote servers; use http when possible)

Header values support ${ENV_VAR} substitution from environment.

Requires: pip install gemcode[mcp]
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig

_ENV_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _expand_env(value: str) -> str:
  """Expand ${VAR} placeholders from environment variables."""
  return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)


def _expand_headers(headers: dict) -> dict:
  return {k: _expand_env(str(v)) for k, v in headers.items()}


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
    prefix = s.get("name") or "mcp"
    tool_filter: list[str] | None = s.get("tools") or None  # optional allowlist

    # ── stdio (local subprocess) ─────────────────────────────────────────
    if "stdio" in s:
      stdio = s["stdio"]
      cmd = stdio.get("command")
      args = stdio.get("args") or []
      env_extra = {k: _expand_env(str(v)) for k, v in (stdio.get("env") or {}).items()}
      if not cmd:
        continue
      params = StdioServerParameters(
          command=cmd,
          args=args,
          env={**os.environ, **env_extra} if env_extra else None,
      )
      kw: dict[str, Any] = dict(connection_params=params, tool_name_prefix=prefix)
      if tool_filter:
        kw["tool_filter"] = tool_filter
      toolsets.append(McpToolset(**kw))
      continue

    # ── http (Streamable HTTP — modern, Cloud Run-friendly) ──────────────
    if "http" in s:
      http_cfg = s["http"]
      url = _expand_env(http_cfg.get("url", ""))
      if not url:
        continue
      headers = _expand_headers(http_cfg.get("headers") or {})
      try:
        from google.adk.tools.mcp_tool.mcp_session_manager import (
            StreamableHTTPConnectionParams,
        )
        params_http = StreamableHTTPConnectionParams(url=url, headers=headers or None)
        kw = dict(connection_params=params_http, tool_name_prefix=prefix)
        if tool_filter:
          kw["tool_filter"] = tool_filter
        toolsets.append(McpToolset(**kw))
      except ImportError:
        # Fallback: try SseConnectionParams for older ADK builds
        try:
          from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams
          params_sse_fb = SseConnectionParams(url=url, headers=headers or None)
          kw = dict(connection_params=params_sse_fb, tool_name_prefix=prefix)
          if tool_filter:
            kw["tool_filter"] = tool_filter
          toolsets.append(McpToolset(**kw))
        except ImportError:
          pass  # ADK version doesn't support remote MCP — skip silently
      continue

    # ── sse (Server-Sent Events — older remote servers) ──────────────────
    if "sse" in s:
      sse_cfg = s["sse"]
      url = _expand_env(sse_cfg.get("url", ""))
      if not url:
        continue
      headers = _expand_headers(sse_cfg.get("headers") or {})
      try:
        from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams
        params_sse = SseConnectionParams(url=url, headers=headers or None)
        kw = dict(connection_params=params_sse, tool_name_prefix=prefix)
        if tool_filter:
          kw["tool_filter"] = tool_filter
        toolsets.append(McpToolset(**kw))
      except ImportError:
        pass  # ADK version too old — skip
      continue

  return toolsets
