"""
Claude Code–style context pressure signals (cf. `autoCompact.ts` / `calculateTokenWarningState`).

Uses API `prompt_token_count` when available; thresholds are token-based with
env overrides. Mirrors Claude’s buffer constants where practical.
"""

from __future__ import annotations

import os

from gemcode.config import GemCodeConfig

# Claude `autoCompact.ts` defaults
AUTOCOMPACT_BUFFER_TOKENS = 13_000
WARNING_THRESHOLD_BUFFER_TOKENS = 20_000
ERROR_THRESHOLD_BUFFER_TOKENS = 20_000
MANUAL_COMPACT_BUFFER_TOKENS = 3_000


def _opt_int(name: str, default: int) -> int:
  raw = os.environ.get(name)
  if raw is None or not str(raw).strip():
    return default
  try:
    return int(str(raw).strip())
  except ValueError:
    return default


def get_effective_context_window_tokens(model: str) -> int:
  """Upper bound on *input* context for threshold math (override via env)."""
  v = _opt_int("GEMCODE_CONTEXT_WINDOW_TOKENS", 0)
  if v > 0:
    return v
  ml = (model or "").lower()
  if "gemini-3" in ml or "gemini-2.5" in ml or "gemini-2" in ml:
    return 1_000_000
  return 200_000


def get_reserved_summary_tokens(model: str) -> int:
  """Claude reserves headroom for compaction summary output; we use a small cap."""
  return min(_opt_int("GEMCODE_AUTOCOMPACT_RESERVED_OUTPUT_TOKENS", 20_000), 20_000)


def get_effective_context_window_size_tokens(model: str) -> int:
  """`effectiveContextWindow` ≈ window minus reserved output (Claude `getEffectiveContextWindowSize`)."""
  w = get_effective_context_window_tokens(model)
  return max(10_000, w - get_reserved_summary_tokens(model))


def get_auto_compact_threshold_tokens(model: str) -> int:
  buf = _opt_int("GEMCODE_AUTOCOMPACT_BUFFER_TOKENS", AUTOCOMPACT_BUFFER_TOKENS)
  return max(0, get_effective_context_window_size_tokens(model) - buf)


def is_autocompact_enabled(cfg: GemCodeConfig | None) -> bool:
  if os.environ.get("GEMCODE_AUTOCOMPACT", "").lower() in ("0", "false", "no", "off"):
    return False
  if cfg is not None and os.environ.get("GEMCODE_AUTOCOMPACT") is None:
    return True
  return os.environ.get("GEMCODE_AUTOCOMPACT", "1").lower() in ("1", "true", "yes", "on")


def calculate_context_warning_state(
    *,
    prompt_token_count: int,
    model: str,
    cfg: GemCodeConfig | None = None,
) -> dict[str, object]:
  """
  Returns keys aligned with Claude’s `calculateTokenWarningState`:
  - percent_left
  - is_above_warning_threshold
  - is_above_error_threshold
  - is_above_auto_compact_threshold
  - is_at_blocking_limit
  """
  auto_on = is_autocompact_enabled(cfg)
  effective = get_effective_context_window_size_tokens(model)
  auto_thr = get_auto_compact_threshold_tokens(model)
  threshold = auto_thr if auto_on else effective

  pct = 0
  if threshold > 0:
    pct = max(0, min(100, round(((threshold - prompt_token_count) / threshold) * 100)))

  warn_buf = _opt_int(
      "GEMCODE_CONTEXT_WARNING_BUFFER_TOKENS", WARNING_THRESHOLD_BUFFER_TOKENS
  )
  err_buf = _opt_int(
      "GEMCODE_CONTEXT_ERROR_BUFFER_TOKENS", ERROR_THRESHOLD_BUFFER_TOKENS
  )
  manual_buf = _opt_int(
      "GEMCODE_CONTEXT_BLOCKING_BUFFER_TOKENS", MANUAL_COMPACT_BUFFER_TOKENS
  )

  warning_threshold = threshold - warn_buf
  error_threshold = threshold - err_buf
  blocking_limit = effective - manual_buf

  return {
      "percent_left": int(pct),
      "is_above_warning_threshold": prompt_token_count >= warning_threshold,
      "is_above_error_threshold": prompt_token_count >= error_threshold,
      "is_above_auto_compact_threshold": auto_on and prompt_token_count >= auto_thr,
      "is_at_blocking_limit": prompt_token_count >= blocking_limit,
      "threshold_tokens": int(threshold),
      "auto_compact_threshold_tokens": int(auto_thr),
      "effective_window_tokens": int(effective),
      "blocking_limit_tokens": int(blocking_limit),
      "warning_threshold_tokens": int(warning_threshold),
      "error_threshold_tokens": int(error_threshold),
  }


def worst_alert_level(state: dict[str, object]) -> int:
  """0=ok, 1=warning, 2=error, 3=blocking."""
  if bool(state.get("is_at_blocking_limit")):
    return 3
  if bool(state.get("is_above_error_threshold")):
    return 2
  if bool(state.get("is_above_warning_threshold")):
    return 1
  return 0
