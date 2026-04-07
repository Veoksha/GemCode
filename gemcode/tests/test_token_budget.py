"""Parity tests for query/token_budget behavior."""

from gemcode.query.token_budget import (
  BudgetTracker,
  check_token_budget,
  create_budget_tracker,
  get_budget_continuation_message,
)


def test_check_token_budget_disabled_for_subagent() -> None:
  bt = create_budget_tracker()
  d = check_token_budget(bt, "sub-1", 100_000, 50_000)
  assert d.action == "stop"
  assert d.completion_event is None


def test_check_token_budget_disabled_without_budget() -> None:
  bt = create_budget_tracker()
  d = check_token_budget(bt, None, None, 50_000)
  assert d.action == "stop"


def test_continuation_under_threshold() -> None:
  bt = BudgetTracker(
      continuation_count=0,
      last_delta_tokens=0,
      last_global_turn_tokens=0,
      started_at_ms=0,
  )
  d = check_token_budget(bt, None, 1_000_000, 100_000)
  assert d.action == "continue"
  assert "token target" in d.nudge_message
  assert bt.continuation_count == 1


def test_get_budget_continuation_message_format() -> None:
  s = get_budget_continuation_message(45, 450_000, 1_000_000)
  assert "45%" in s
  assert "450,000" in s
  assert "1,000,000" in s
