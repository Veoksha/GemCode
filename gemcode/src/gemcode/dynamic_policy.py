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


def _risk(cfg) -> float:
  try:
    v = getattr(cfg, "_risk_score", None)
    if isinstance(v, (int, float)):
      return float(v)
  except Exception:
    return 0.0
  return 0.0


def _clamp(x: float, lo: float, hi: float) -> float:
  return lo if x < lo else hi if x > hi else x


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

  Then apply a risk-based boost (if enabled) so complex tasks stay evidence-rich.
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

  # Risk boost: scale caps upward for risky tasks, but keep bounded.
  risk_enabled = _truthy(getattr(cfg, "dynamic_risk_policy", True) if cfg is not None else True, default=True)
  risk_boost = float(getattr(cfg, "dynamic_risk_boost", 0.6) if cfg is not None else 0.6)
  risk_score = _risk(cfg) if (cfg is not None and risk_enabled) else 0.0
  risk_score = _clamp(risk_score, 0.0, 1.0)
  boost = 1.0 + (_clamp(risk_boost, 0.0, 1.5) * risk_score)

  def _scale(n: int, *, cap: int) -> int:
    return min(cap, max(1000, int(n * boost)))

  if pct >= 45:
    mult = 1.4
    return DynamicCaps(
      tool_inline_chars=_scale(min(24_000, int(base_tool * mult)), cap=30_000),
      read_file_max_bytes=min(200_000, int(140_000 * boost)),
      web_fetch_max_chars=min(60_000, int(30_000 * boost)),
      bash_stdout_chars=min(80_000, int(30_000 * boost)),
      bash_stderr_chars=min(40_000, int(15_000 * boost)),
      run_stdout_chars=min(80_000, int(30_000 * boost)),
      run_stderr_chars=min(80_000, int(30_000 * boost)),
      grep_max_matches=min(200, int(60 * boost)),
    )

  if pct >= 20:
    mult = 1.0
    return DynamicCaps(
      tool_inline_chars=_scale(min(18_000, int(base_tool * mult)), cap=24_000),
      read_file_max_bytes=min(160_000, int(80_000 * boost)),
      web_fetch_max_chars=min(40_000, int(20_000 * boost)),
      bash_stdout_chars=min(50_000, int(20_000 * boost)),
      bash_stderr_chars=min(30_000, int(10_000 * boost)),
      run_stdout_chars=min(50_000, int(20_000 * boost)),
      run_stderr_chars=min(50_000, int(20_000 * boost)),
      grep_max_matches=min(120, int(40 * boost)),
    )

  # Tight
  mult = 0.6
  return DynamicCaps(
    tool_inline_chars=max(2000, min(12_000, int(base_tool * mult * boost))),
    read_file_max_bytes=min(90_000, int(35_000 * boost)),
    web_fetch_max_chars=min(20_000, int(10_000 * boost)),
    bash_stdout_chars=min(25_000, int(10_000 * boost)),
    bash_stderr_chars=min(20_000, int(8_000 * boost)),
    run_stdout_chars=min(25_000, int(10_000 * boost)),
    run_stderr_chars=min(25_000, int(10_000 * boost)),
    grep_max_matches=min(80, int(20 * boost)),
  )

