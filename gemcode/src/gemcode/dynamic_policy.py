"""
Dynamic token budgeting / caps.

Optimization must not make the agent dumb:
- When context pressure is low, allow richer tool outputs and wider reads.
- When context pressure is high, tighten caps and offload aggressively.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _truthy(v: Any, *, default: bool = False) -> bool:
  if v is None:
    return default
  if isinstance(v, bool):
    return v
  if isinstance(v, str):
    return v.lower() in ("1", "true", "yes", "on")
  return bool(v)


def _pct_left(cfg) -> int | None:
  try:
    v = getattr(cfg, "_context_percent_left", None)
    if isinstance(v, int):
      return v
  except Exception:
    return None
  return None


@dataclass(frozen=True)
class DynamicCaps:
  tool_inline_chars: int
  read_file_max_bytes: int
  web_fetch_max_chars: int
  bash_stdout_chars: int
  bash_stderr_chars: int
  run_stdout_chars: int
  run_stderr_chars: int
  grep_max_matches: int


def get_dynamic_caps(cfg) -> DynamicCaps:
  """
  Compute caps based on current context pressure.

  Policy:
  - Healthy (>=45% left): generous caps (better evidence, less re-asking).
  - Warning (20-44%): moderate caps.
  - Tight (<20%): strict caps + prefer offload.
  """
  # cfg can be None in some tool contexts; treat as enabled with defaults.
  enabled = _truthy(getattr(cfg, "dynamic_token_policy", True) if cfg is not None else True, default=True)
  if not enabled:
    # Essentially "no-op" high caps; tools still apply their explicit maxes.
    return DynamicCaps(
      tool_inline_chars=int(getattr(cfg, "tool_result_max_chars", 12000) or 12000),
      read_file_max_bytes=200_000,
      web_fetch_max_chars=40_000,
      bash_stdout_chars=80_000,
      bash_stderr_chars=20_000,
      run_stdout_chars=50_000,
      run_stderr_chars=50_000,
      grep_max_matches=80,
    )

  pct = _pct_left(cfg) if cfg is not None else None
  if pct is None:
    pct = 35

  # Base knobs from config (so users can still tune globally).
  base_tool = int(getattr(cfg, "tool_result_max_chars", 12000) or 12000) if cfg is not None else 12000
  base_tool = max(1000, base_tool)

  if pct >= 45:
    mult = 1.4
    return DynamicCaps(
      tool_inline_chars=min(24_000, int(base_tool * mult)),
      read_file_max_bytes=140_000,
      web_fetch_max_chars=30_000,
      bash_stdout_chars=30_000,
      bash_stderr_chars=15_000,
      run_stdout_chars=30_000,
      run_stderr_chars=30_000,
      grep_max_matches=60,
    )

  if pct >= 20:
    mult = 1.0
    return DynamicCaps(
      tool_inline_chars=min(18_000, int(base_tool * mult)),
      read_file_max_bytes=80_000,
      web_fetch_max_chars=20_000,
      bash_stdout_chars=20_000,
      bash_stderr_chars=10_000,
      run_stdout_chars=20_000,
      run_stderr_chars=20_000,
      grep_max_matches=40,
    )

  # Tight
  mult = 0.6
  return DynamicCaps(
    tool_inline_chars=max(2000, int(base_tool * mult)),
    read_file_max_bytes=35_000,
    web_fetch_max_chars=10_000,
    bash_stdout_chars=10_000,
    bash_stderr_chars=8_000,
    run_stdout_chars=10_000,
    run_stderr_chars=10_000,
    grep_max_matches=20,
  )

