from __future__ import annotations

from typing import Any

from gemcode.config import GemCodeConfig


def make_curated_memory_tools(cfg: GemCodeConfig) -> list:
  """
  Curated memory: safe, small facts that can be re-injected.
  """

  def remember_fact(text: str, target: str = "memory") -> dict[str, Any]:
    """
    Append a durable, non-sensitive fact to curated memory.

    target:
      - "memory": project facts, commands, conventions
      - "user": user preferences for this repo
    """
    from gemcode.curated_memory import append_fact

    return append_fact(cfg.project_root, target=target, text=text)

  def read_curated_memory(max_chars: int = 6000) -> dict[str, Any]:
    """Read curated memory snapshot (GEMCODE_MEMORY.md + GEMCODE_USER.md)."""
    from gemcode.curated_memory import load_snapshot

    return load_snapshot(cfg.project_root, max_chars=max_chars)

  remember_fact.__name__ = "remember_fact"
  read_curated_memory.__name__ = "read_curated_memory"
  return [remember_fact, read_curated_memory]

