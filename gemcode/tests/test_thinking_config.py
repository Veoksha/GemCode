from google.genai import types

from gemcode.config import GemCodeConfig
from gemcode.thinking import build_thinking_config


def test_disable_thinking_gemini3_uses_minimal(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-3-flash-preview"
  cfg.disable_thinking = True
  cfg.include_thought_summaries = False
  assert build_thinking_config(cfg) == types.ThinkingConfig(
    thinking_level="minimal",
    include_thoughts=None,
  )


def test_disable_thinking_gemini25_uses_budget_zero(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-2.5-flash"
  cfg.disable_thinking = True
  cfg.include_thought_summaries = False
  assert build_thinking_config(cfg) == types.ThinkingConfig(
    thinking_budget=0,
    include_thoughts=None,
  )


def test_include_thought_summaries_only_gemini25(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-2.5-flash"
  cfg.disable_thinking = False
  cfg.include_thought_summaries = True
  cfg.thinking_level = None
  cfg.thinking_budget = None
  thinking_cfg = build_thinking_config(cfg)
  assert thinking_cfg == types.ThinkingConfig(include_thoughts=True)


def test_include_thought_summaries_gemini3_sets_level(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-3.1-pro-preview"
  cfg.disable_thinking = False
  cfg.include_thought_summaries = True
  cfg.show_full_thinking = False
  thinking_cfg = build_thinking_config(cfg)
  assert thinking_cfg == types.ThinkingConfig(
    include_thoughts=True,
    thinking_level="medium",
  )

  cfg.show_full_thinking = True
  thinking_cfg = build_thinking_config(cfg)
  assert thinking_cfg == types.ThinkingConfig(
    include_thoughts=True,
    thinking_level="high",
  )


def test_thinking_level_is_ignored_for_gemini25(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-2.5-flash"
  cfg.disable_thinking = False
  cfg.model_mode = "auto"  # Avoid mode-based defaults so override-ignoring is testable.
  cfg.include_thought_summaries = False
  cfg.thinking_level = "high"
  cfg.thinking_budget = None
  assert build_thinking_config(cfg) is None


def test_disable_thinking_gemini3_pro_uses_low(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-3.1-pro-preview"
  cfg.disable_thinking = True
  cfg.include_thought_summaries = False
  assert build_thinking_config(cfg) == types.ThinkingConfig(
    thinking_level="low",
    include_thoughts=None,
  )


def test_mode_defaults_map_to_thinking_config_gemini3(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-3-flash-preview"
  cfg.disable_thinking = False
  cfg.include_thought_summaries = False
  cfg.thinking_level = None
  cfg.thinking_budget = None

  cfg.model_mode = "fast"
  assert build_thinking_config(cfg) == types.ThinkingConfig(
    thinking_level="minimal",
    include_thoughts=None,
  )

  cfg.model_mode = "balanced"
  assert build_thinking_config(cfg) == types.ThinkingConfig(
    thinking_level="medium",
    include_thoughts=None,
  )

  cfg.model_mode = "quality"
  assert build_thinking_config(cfg) == types.ThinkingConfig(
    thinking_level="high",
    include_thoughts=None,
  )


def test_thinking_budget_applied_for_gemini25(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-2.5-flash"
  cfg.disable_thinking = False
  cfg.include_thought_summaries = False
  cfg.thinking_level = None
  cfg.thinking_budget = -1
  assert build_thinking_config(cfg) == types.ThinkingConfig(
    thinking_budget=-1,
    include_thoughts=None,
  )

