"""
Gemini thinking configuration helper.

Conceptual mapping to Claude Code:
- thinking is enabled by default (Gemini adaptive/dynamic)
- only when user explicitly disables or sets a budget/level we override
- Gemini 3 uses `thinkingLevel`; Gemini 2.5 uses `thinkingBudget`
"""

from __future__ import annotations

from typing import Optional

from google.genai import types

from gemcode.config import GemCodeConfig


def _is_gemini_2_5_series(model_id: str) -> bool:
  return "2.5" in (model_id or "")


def _is_gemini_3_pro(model_id: str) -> bool:
  # Gemini 3.1 Pro preview doesn't support `thinkingLevel=minimal`.
  return "3.1-pro" in (model_id or "") or "3-pro" in (model_id or "")


def build_thinking_config(cfg: GemCodeConfig) -> Optional[types.ThinkingConfig]:
  """
  Returns a `types.ThinkingConfig` to pass to ADK's LlmAgent.generate_content_config.

  If `None` is returned, GemCode lets Gemini use its default dynamic thinking
  behavior (Claude-like adaptive default).
  """
  model_id = getattr(cfg, "model", "") or ""
  is_25 = _is_gemini_2_5_series(model_id)

  # Claude-like disable semantics:
  # - Gemini 3 can't fully disable, so approximate with `minimal`.
  # - Gemini 2.5 can be disabled by setting thinkingBudget=0 (if supported).
  disable = bool(getattr(cfg, "disable_thinking", False))

  include = bool(getattr(cfg, "include_thought_summaries", False))
  thinking_level = getattr(cfg, "thinking_level", None)
  thinking_budget = getattr(cfg, "thinking_budget", None)

  if disable:
    if is_25:
      # 2.5 Pro doesn't support fully disabling thinking.
      if "2.5-pro" in model_id:
        return types.ThinkingConfig(
          thinking_budget=512,
          include_thoughts=include or None,
        )
      if "flash-lite" in model_id:
        return types.ThinkingConfig(
          thinking_budget=0,
          include_thoughts=include or None,
        )
      return types.ThinkingConfig(
        thinking_budget=0,
        include_thoughts=include or None,
      )
    # Gemini 3: minimal is the closest available "disable" knob.
    if _is_gemini_3_pro(model_id):
      return types.ThinkingConfig(
        thinking_level="low",
        include_thoughts=include or None,
      )
    return types.ThinkingConfig(
      thinking_level="minimal",
      include_thoughts=include or None,
    )

  # Explicit user overrides take precedence.
  if thinking_level is not None and not is_25:
    level = str(thinking_level)
    if _is_gemini_3_pro(model_id) and level == "minimal":
      level = "low"
    return types.ThinkingConfig(
      thinking_level=level,
      include_thoughts=include or None,
    )

  if thinking_budget is not None and is_25:
    return types.ThinkingConfig(
      thinking_budget=int(thinking_budget),
      include_thoughts=include or None,
    )

  # If the user only wants thought summaries, we can set include_thoughts
  # without forcing a budget/level.
  if include:
    return types.ThinkingConfig(include_thoughts=True)

  # Otherwise: Claude-like auto mapping based on model_mode.
  mode = (getattr(cfg, "model_mode", "auto") or "auto").lower()
  if mode == "auto":
    # Let Gemini choose its dynamic/adaptive thinking.
    return None

  if not is_25:
    # Gemini 3 thinkingLevel mapping.
    if mode == "fast":
      return types.ThinkingConfig(
        thinking_level="low" if _is_gemini_3_pro(model_id) else "minimal",
        include_thoughts=None,
      )
    if mode == "balanced":
      return types.ThinkingConfig(
        thinking_level="medium",
        include_thoughts=None,
      )
    # quality
    return types.ThinkingConfig(
      thinking_level="high",
      include_thoughts=None,
    )

  # Gemini 2.5 thinkingBudget mapping.
  if mode == "fast":
    if "2.5-pro" in model_id:
      return types.ThinkingConfig(thinking_budget=512, include_thoughts=None)
    # flash/flash-preview/flash-lite
    return types.ThinkingConfig(
      thinking_budget=0 if "flash-lite" in model_id else 0, include_thoughts=None
    )
  if mode == "balanced":
    if "2.5-pro" in model_id:
      return types.ThinkingConfig(thinking_budget=4096, include_thoughts=None)
    return types.ThinkingConfig(thinking_budget=1024, include_thoughts=None)

  # quality
  # For 2.5 Pro this remains dynamic because disable isn't supported.
  return types.ThinkingConfig(thinking_budget=-1, include_thoughts=None)

