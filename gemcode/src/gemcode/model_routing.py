"""
Model routing for GemCode.

Goal: match Claude-style "multi modes" where users can select a model mode
explicitly, or GemCode can choose a best-fit model automatically (no extra
model call; heuristic only).
"""

from __future__ import annotations

import re
from typing import Literal

from gemcode.config import GemCodeConfig

ModelMode = Literal["auto", "fast", "balanced", "quality"]
FamilyMode = Literal["auto", "primary", "alt"]


def _contains_any(haystack: str, needles: list[str]) -> bool:
  h = haystack.lower()
  return any(n in h for n in needles)


def pick_effective_model(cfg: GemCodeConfig, prompt: str) -> str:
  """
  Returns the effective model id to use for this run.

  Heuristic rules (cheap, deterministic):
  - quality for architecture/design/refactor/complex trade-offs
  - fast for small edits, tool-driven tasks, or quick debugging
  - balanced as the default otherwise
  """
  mode_norm = (getattr(cfg, "model_mode", "fast") or "fast").lower()

  # If the user explicitly picked a model id, honor it.
  if getattr(cfg, "model_overridden", False):
    return cfg.model

  # Optional deep research routing: when user asks explicitly (flag) or when
  # `model_mode=auto` and the prompt requests research-like output.
  deep_research_triggers = [
    "deep research",
    "deep-dive",
    "research",
    "sources",
    "citations",
    "grounded",
    "investigate",
    "literature",
    "benchmark",
  ]
  prompt_norm = re.sub(r"\s+", " ", prompt or "").strip().lower()
  if getattr(cfg, "enable_deep_research", False):
    return getattr(cfg, "model_deep_research", None) or cfg.model
  if mode_norm == "auto" and _contains_any(prompt_norm, deep_research_triggers):
    return getattr(cfg, "model_deep_research", None) or cfg.model

  # Capability precedence: computer-use model selection.
  # (Deep research already handled above.)
  if getattr(cfg, "enable_audio", False):
    return getattr(cfg, "model_audio_live", None) or cfg.model
  # Only switch to the computer-use model when Playwright is actually available.
  # `_computer_use_available` is set False by session_runtime when the probe fails,
  # preventing an HTTP 400 ("model requires Computer Use tool") from the API.
  if getattr(cfg, "enable_computer_use", False) and getattr(cfg, "_computer_use_available", True):
    return getattr(cfg, "model_computer_use", None) or cfg.model

  primary_fast = cfg.model
  primary_quality = getattr(cfg, "model_quality", None) or primary_fast
  primary_balanced = getattr(cfg, "model_balanced", None) or primary_fast

  alt_fast = getattr(cfg, "model_alt", None) or primary_fast
  alt_quality = getattr(cfg, "model_alt_quality", None) or primary_quality
  alt_balanced = getattr(cfg, "model_alt_balanced", None) or primary_balanced

  if mode_norm not in ("auto", "fast", "balanced", "quality"):
    return primary_fast

  def decide_base_mode() -> Literal["fast", "balanced", "quality"]:
    if mode_norm == "fast":
      return "fast"
    if mode_norm == "balanced":
      return "balanced"
    if mode_norm == "quality":
      return "quality"

    # auto mode: choose base mode using prompt heuristics.
    p_norm = re.sub(r"\s+", " ", prompt or "").strip()
    plen = len(p_norm)

    quality_triggers = [
      "architecture",
      "design",
      "system",
      "refactor",
      "trade",
      "complex",
      "scal",
      "performance",
      "profil",
      "migration",
      "schema",
      "how would you",
      "deep dive",
    ]
    fast_triggers = [
      "fix",
      "bug",
      "error",
      "tests",
      "pytest",
      "debug",
      "failing",
      "lint",
      "format",
      "quick",
      "small change",
    ]

    if (
      plen > 2_000
      and _contains_any(p_norm, ["design", "architecture", "refactor", "trade"])
    ):
      return "quality"

    if _contains_any(p_norm, quality_triggers):
      return "quality"

    if _contains_any(p_norm, fast_triggers):
      return "fast"

    # If prompt is long but not explicitly complex, balanced tends to be safer.
    if plen > 4_000:
      return "balanced"

    return "balanced"

  base_mode = decide_base_mode()

  # Decide model family (primary vs 2.5-alt).
  fam = (getattr(cfg, "model_family_mode", "auto") or "auto").lower()
  if fam not in ("auto", "primary", "alt"):
    fam = "auto"

  p_norm2 = re.sub(r"\s+", " ", prompt or "").strip()
  # Reuse quality triggers: complex prompts get primary (3.x); simpler prompts
  # prefer alt (2.5) by default in `auto` family mode.
  complex_triggers = [
    "architecture",
    "design",
    "system",
    "refactor",
    "trade",
    "complex",
    "performance",
    "migration",
    "schema",
    "deep dive",
  ]

  choose_primary: bool
  if fam == "primary":
    choose_primary = True
  elif fam == "alt":
    choose_primary = False
  else:
    choose_primary = _contains_any(p_norm2, complex_triggers)

  if choose_primary:
    if base_mode == "fast":
      return primary_fast
    if base_mode == "balanced":
      return primary_balanced
    return primary_quality

  if base_mode == "fast":
    return alt_fast
  if base_mode == "balanced":
    return alt_balanced
  return alt_quality

