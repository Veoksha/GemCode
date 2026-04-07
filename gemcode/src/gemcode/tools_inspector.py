"""
GemCode tool inventory + declaration smoke testing.

tool-system prompt emphasizes:
- inventory of available tools (feature-gated vs always-on)
- schema/declaration compilation smoke tests per tool
- permission/category metadata

In GemCode (ADK-based), tool declarations are produced by ADK via:
- wrapping callables in `google.adk.tools.function_tool.FunctionTool`
- calling `_get_declaration()` on BaseTool implementations

This module provides a deterministic way to:
- enumerate tools for a given `GemCodeConfig`
- validate each tool can build its declaration (when supported)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.function_tool import FunctionTool

from gemcode.config import GemCodeConfig
from gemcode.modality_tools import build_extra_tools as build_modality_tools
from gemcode.tools import build_function_tools


@dataclass(frozen=True)
class ToolInspection:
  name: str
  category: str
  declaration_present: bool
  declaration_error: str | None = None
  tool_type: str = "callable"  # "callable" | "builtin"


def _tool_name(tool_union: Any) -> str:
  if isinstance(tool_union, BaseTool):
    return getattr(tool_union, "name", "") or ""
  if callable(tool_union):
    return getattr(tool_union, "__name__", "") or ""
  return ""


def _classify_tool(name: str) -> str:
  # Keep this consistent with gemcode/src/gemcode/tool_registry.py
  from gemcode.tool_registry import READ_ONLY_TOOLS, MUTATING_TOOLS, SHELL_TOOLS

  if name in READ_ONLY_TOOLS:
    return "read_only"
  if name in MUTATING_TOOLS:
    return "mutating"
  if name in SHELL_TOOLS:
    return "shell"
  return "other"


def _iter_tool_unions(cfg: GemCodeConfig, *, extra_tools: Iterable[Any] | None = None):
  # Core function tools (always available for the agent).
  tools: list[Any] = list(build_function_tools(cfg))

  # Deep research built-in tools (Search/URL/Maps).
  tools.extend(build_modality_tools(cfg))

  # Optional MCP toolsets are provided as `extra_tools` by the caller (CLI).
  if extra_tools:
    tools.extend(list(extra_tools))

  # Note: ComputerUseToolset requires launching Playwright via BrowserComputer.
  # Tool declaration smoke tests can be slow and brittle, so we intentionally
  # omit it from inventory unless the caller constructs it already.
  #
  # If you want it included, pass it via `extra_tools` explicitly.
  return tools


def inspect_tools(
  cfg: GemCodeConfig,
  *,
  extra_tools: Iterable[Any] | None = None,
) -> list[ToolInspection]:
  """
  Enumerate tools for this config and attempt to compile each tool's
  declaration (schema) without executing tool logic.
  """
  out: list[ToolInspection] = []

  for tool_union in _iter_tool_unions(cfg, extra_tools=extra_tools):
    name = _tool_name(tool_union) or "<unknown>"
    category = _classify_tool(name)
    tool_type = "builtin" if isinstance(tool_union, BaseTool) else "callable"

    declaration_present = False
    declaration_error: str | None = None

    try:
      if isinstance(tool_union, BaseTool):
        decl = tool_union._get_declaration()
      else:
        # For pure callables, ADK uses FunctionTool to build FunctionDeclaration.
        decl_tool = FunctionTool(func=tool_union)  # type: ignore[arg-type]
        decl = decl_tool._get_declaration()

      declaration_present = decl is not None
    except Exception as e:
      declaration_present = False
      declaration_error = f"{type(e).__name__}: {e}"

    out.append(
      ToolInspection(
        name=name,
        category=category,
        declaration_present=declaration_present,
        declaration_error=declaration_error,
        tool_type=tool_type,
      )
    )

  # Stable output ordering.
  out.sort(key=lambda x: (x.category, x.name.lower()))
  return out


def smoke_tools(
  inspections: list[ToolInspection],
) -> list[ToolInspection]:
  """Return only the tools that failed declaration compilation."""
  return [i for i in inspections if i.declaration_error is not None]

