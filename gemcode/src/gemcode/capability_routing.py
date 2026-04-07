"""
Capability-based routing (style conceptually).

This layer decides which *capabilities* to enable (deep research tools,
embeddings retrieval, computer-use tools) and leaves the existing familiar
outer/inner loops intact.

It is intentionally conservative:
- It only enables capabilities (turns them on), it does not disable
  explicitly requested capabilities.
- Computer-use is NEVER auto-enabled from prompt heuristics.  It requires
  an explicit user action (/computer on or capability_mode=computer) because
  it switches the model, launches a browser, and blocks file-system tools.
- Computer-use model selection is enforced at model-routing precedence, and
  tool execution remains permission-gated via `callbacks.py`.
"""

from __future__ import annotations

import re

from gemcode.config import GemCodeConfig

CapabilityMode = str  # "auto|research|embeddings|computer|audio|all"


_RESEARCH_TRIGGERS = [
  "deep research",
  "deep-dive",
  "research",
  "citations",
  "sources",
  "grounded",
  "investigate",
  "literature",
  "benchmark",
]

_EMBEDDINGS_TRIGGERS = [
  "embedding",
  "embeddings",
  "semantic search",
  "similarity",
  "vector",
  "rag",
  "retrieve",
  "relevant docs",
]

_AUDIO_TRIGGERS = [
  "audio",
  "voice",
  "microphone",
  "speak",
  "listen",
  "tts",
  "tts preview",
]


def _contains_any(haystack: str, needles: list[str]) -> bool:
  h = haystack.lower()
  return any(n in h for n in needles)


def apply_capability_routing(
  cfg: GemCodeConfig,
  prompt: str,
  *,
  context: str = "prompt",
) -> None:
  """
  Mutates `cfg` in-place:
  - sets `enable_deep_research`, `enable_embeddings`, `enable_computer_use`
  - sets `enable_audio` only when context is `live-audio`
  """
  mode = (getattr(cfg, "capability_mode", "auto") or "auto").lower()
  p_norm = re.sub(r"\s+", " ", prompt or "").strip().lower()

  def enable_research() -> None:
    cfg.enable_deep_research = True

  def enable_embeddings() -> None:
    cfg.enable_embeddings = True

  def enable_computer() -> None:
    # Respect session-level unavailability (e.g. Playwright not installed).
    # When computer-use is known-broken, auto-routing must not re-enable it.
    if getattr(cfg, "_computer_use_available", True) is False:
      return
    cfg.enable_computer_use = True

  def enable_audio() -> None:
    if context == "live-audio":
      cfg.enable_audio = True

  # User-selected mode.
  if mode == "research":
    enable_research()
    return
  if mode == "embeddings":
    enable_embeddings()
    return
  if mode == "computer":
    enable_computer()
    return
  if mode == "audio":
    enable_audio()
    return
  if mode == "all":
    enable_research()
    enable_embeddings()
    enable_computer()
    enable_audio()
    return

  # Auto mode: prompt heuristics.
  # NOTE: computer-use is deliberately NOT auto-triggered here.
  # It must be enabled explicitly via /computer on or capability_mode=computer.
  # Auto-enabling it causes the model to switch to the computer-use preview
  # for any prompt containing words like "navigate"/"scroll"/"browser",
  # which blocks normal file and code tools for completely unrelated tasks.
  if mode == "auto":
    if _contains_any(p_norm, _RESEARCH_TRIGGERS):
      enable_research()
    if _contains_any(p_norm, _EMBEDDINGS_TRIGGERS):
      enable_embeddings()
    if _contains_any(p_norm, _AUDIO_TRIGGERS):
      enable_audio()
    return

  # Unknown mode: do nothing.
  return

