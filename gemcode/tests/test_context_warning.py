"""Tests for Claude-style context warning thresholds."""

from __future__ import annotations

import os

from gemcode.config import GemCodeConfig
from gemcode.context_warning import (
  calculate_context_warning_state,
  get_auto_compact_threshold_tokens,
  get_effective_context_window_size_tokens,
  worst_alert_level,
)


def test_effective_window_respects_env(monkeypatch) -> None:
  monkeypatch.setenv("GEMCODE_CONTEXT_WINDOW_TOKENS", "100000")
  assert get_effective_context_window_size_tokens("gemini-3-flash") < 100000


def test_calculate_context_warning_state_levels() -> None:
  cfg = GemCodeConfig(project_root=__import__("pathlib").Path("."))
  os.environ.pop("GEMCODE_CONTEXT_WINDOW_TOKENS", None)
  eff = get_effective_context_window_size_tokens("gemini-3-flash")
  aut = get_auto_compact_threshold_tokens("gemini-3-flash")
  # Below autocompact threshold → high percent_left, level 0
  s0 = calculate_context_warning_state(
      prompt_token_count=max(0, aut - 50_000),
      model="gemini-3-flash",
      cfg=cfg,
  )
  assert worst_alert_level(s0) == 0
  probe = calculate_context_warning_state(
      prompt_token_count=0, model="gemini-3-flash", cfg=cfg
  )
  blocking = int(probe["blocking_limit_tokens"])  # type: ignore[index]
  s_block = calculate_context_warning_state(
      prompt_token_count=blocking,
      model="gemini-3-flash",
      cfg=cfg,
  )
  assert s_block.get("is_at_blocking_limit") is True
