"""
Root LlmAgent definition (Claude Code: agent config + tool list, analogous to tools.ts + prompts).

See `session_runtime.py` for Runner/session wiring (outer layer).
See `tool_registry.py` for tool categories (read vs mutating vs shell).
"""

from __future__ import annotations

import inspect
from pathlib import Path

from google.adk.agents.llm_agent import LlmAgent

from gemcode.callbacks import (
  make_after_model_callback,
  make_after_tool_callback,
  make_before_tool_callback,
  make_on_model_error_callback,
  make_on_tool_error_callback,
)
from gemcode.compaction import make_before_model_callback
from gemcode.config import GemCodeConfig
from gemcode.limits import make_before_model_limits_callback, make_before_model_token_budget_callback
from gemcode.thinking import build_thinking_config
from gemcode.tools import build_function_tools
from gemcode.tool_prompt_manifest import build_tool_manifest


def _chain_before_model_callbacks(*callbacks):
  cbs = [c for c in callbacks if c is not None]
  if not cbs:
    return None
  if len(cbs) == 1:
    return cbs[0]

  async def chained(callback_context, llm_request):
    for cb in cbs:
      out = cb(callback_context, llm_request)
      if inspect.isawaitable(out):
        out = await out
      if out is not None:
        return out
    return None

  return chained


def _load_gemini_md(project_root: Path) -> str:
  for name in ("GEMINI.md", "gemini.md"):
    p = project_root / name
    if p.is_file():
      return p.read_text(encoding="utf-8", errors="replace")[:50_000]
  return ""


def build_instruction(cfg: GemCodeConfig) -> str:
  base = """You are GemCode, an expert software engineering agent.
You work only inside the user's project directory. Use tools to read and explore before editing.
Prefer small, testable edits. Explain assumptions briefly."""

  tool_manifest = build_tool_manifest(cfg)

  if tool_manifest:
    base = f"{base}\n\n{tool_manifest}"
  extra = _load_gemini_md(cfg.project_root)
  if extra.strip():
    return f"{base}\n\n## Project instructions (GEMINI.md)\n{extra}"
  return base


def build_root_agent(cfg: GemCodeConfig, extra_tools: list | None = None) -> LlmAgent:
  """Create the root LlmAgent with tools and callbacks (no Runner)."""
  tools = build_function_tools(cfg)
  if getattr(cfg, "enable_memory", False):
    # ADK preload_memory injects retrieved memories into the next llm_request.
    from google.adk.tools import preload_memory

    tools = [preload_memory, *tools]
  if extra_tools:
    tools = [*tools, *extra_tools]

  before_model = _chain_before_model_callbacks(
      make_before_model_callback(cfg),
      make_before_model_limits_callback(cfg),
      make_before_model_token_budget_callback(cfg),
  )
  cb_kwargs: dict = {
    "before_tool_callback": make_before_tool_callback(cfg),
    "after_tool_callback": make_after_tool_callback(cfg),
    "after_model_callback": make_after_model_callback(cfg),
    "on_tool_error_callback": make_on_tool_error_callback(cfg),
    "on_model_error_callback": make_on_model_error_callback(cfg),
  }
  if before_model is not None:
    cb_kwargs["before_model_callback"] = before_model

  # Claude-like thinking: enabled by default (Gemini dynamic), but allow
  # explicit overrides for disable/budgets/levels.
  gen_cfg = None
  thinking_cfg = build_thinking_config(cfg)
  tool_cfg = None
  model_id = getattr(cfg, "model", "") or ""
  is_gemini_3 = "gemini-3" in model_id.lower()
  comb_mode = (getattr(cfg, "tool_combination_mode", None) or "deep_research").lower()
  enable_for_run = False
  if comb_mode in ("auto", "deep_research"):
    enable_for_run = bool(getattr(cfg, "enable_deep_research", False))
  elif comb_mode == "always":
    enable_for_run = True
  elif comb_mode == "never":
    enable_for_run = False
  else:
    # Unknown values: stay conservative.
    enable_for_run = bool(getattr(cfg, "enable_deep_research", False))

  if enable_for_run and is_gemini_3:
    from google.genai import types

    # Gemini "tool context circulation" enables built-in tools results to
    # be combined with your client-side function tools in the same workflow.
    tool_cfg = types.ToolConfig(include_server_side_tool_invocations=True)

  if thinking_cfg is not None or tool_cfg is not None:
    from google.genai import types

    gen_cfg = types.GenerateContentConfig(
      thinking_config=thinking_cfg,
      tool_config=tool_cfg,
    )

  return LlmAgent(
      model=cfg.model,
      name="gemcode",
      instruction=build_instruction(cfg),
      tools=tools,
      generate_content_config=gen_cfg,
      **cb_kwargs,
  )


def create_runner(cfg: GemCodeConfig, extra_tools: list | None = None):
  """Backward-compatible: prefer `gemcode.session_runtime.create_runner`."""
  from gemcode.session_runtime import create_runner as _cr

  return _cr(cfg, extra_tools=extra_tools)
