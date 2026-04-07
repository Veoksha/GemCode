"""
Per-turn token budget continuation (cf. typical `query/tokenBudget.ts`).

Used with a *single* agent (no sub-agent id): decide whether to inject a
continuation nudge vs stop when cumulative turn tokens approach `budget`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

COMPLETION_THRESHOLD = 0.9
DIMINISHING_THRESHOLD = 500


@dataclass
class BudgetTracker:
  continuation_count: int = 0
  last_delta_tokens: int = 0
  last_global_turn_tokens: int = 0
  started_at_ms: int = 0


def create_budget_tracker() -> BudgetTracker:
  import time

  return BudgetTracker(started_at_ms=int(time.time() * 1000))


@dataclass(frozen=True)
class _ContinueDecision:
  action: Literal["continue"]
  nudge_message: str
  continuation_count: int
  pct: int
  turn_tokens: int
  budget: int


@dataclass(frozen=True)
class _StopDecision:
  action: Literal["stop"]
  completion_event: dict | None


TokenBudgetDecision = _ContinueDecision | _StopDecision


def get_budget_continuation_message(pct: int, turn_tokens: int, budget: int) -> str:
  def fmt(n: int) -> str:
    return f"{n:,}"

  return (
      f"Stopped at {pct}% of token target ({fmt(turn_tokens)} / {fmt(budget)}). "
      "Keep working — do not summarize."
  )


def check_token_budget(
    tracker: BudgetTracker,
    agent_id: str | None,
    budget: int | None,
    global_turn_tokens: int,
) -> TokenBudgetDecision:
  """Same control flow as `checkTokenBudget`."""
  if agent_id or budget is None or budget <= 0:
    return _StopDecision(action="stop", completion_event=None)

  turn_tokens = global_turn_tokens
  pct = min(100, round((turn_tokens / budget) * 100))
  delta_since_last = global_turn_tokens - tracker.last_global_turn_tokens

  is_diminishing = (
      tracker.continuation_count >= 3
      and delta_since_last < DIMINISHING_THRESHOLD
      and tracker.last_delta_tokens < DIMINISHING_THRESHOLD
  )

  if not is_diminishing and turn_tokens < budget * COMPLETION_THRESHOLD:
    tracker.continuation_count += 1
    tracker.last_delta_tokens = delta_since_last
    tracker.last_global_turn_tokens = global_turn_tokens
    return _ContinueDecision(
        action="continue",
        nudge_message=get_budget_continuation_message(pct, turn_tokens, budget),
        continuation_count=tracker.continuation_count,
        pct=pct,
        turn_tokens=turn_tokens,
        budget=budget,
    )

  if is_diminishing or tracker.continuation_count > 0:
    import time

    duration_ms = int(time.time() * 1000) - tracker.started_at_ms
    return _StopDecision(
        action="stop",
        completion_event={
            "continuation_count": tracker.continuation_count,
            "pct": pct,
            "turn_tokens": turn_tokens,
            "budget": budget,
            "diminishing_returns": is_diminishing,
            "duration_ms": duration_ms,
        },
    )

  return _StopDecision(action="stop", completion_event=None)
