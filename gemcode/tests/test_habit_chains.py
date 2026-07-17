"""Tests for habit chain triggers."""

from __future__ import annotations

from pathlib import Path

from gemcode.agent_habits import Habit, load_habits, save_habits
from gemcode.habit_chains import (
  habits_watching,
  render_chain_prompt,
  would_create_habit_cycle,
)


def test_habits_watching_filters_by_upstream(tmp_path: Path) -> None:
  save_habits(
    tmp_path,
    [
      Habit(name="fetch", agent="self", prompt="fetch", every_seconds=60),
      Habit(
        name="summarize",
        agent="self",
        prompt="sum {{source_report}}",
        trigger_after="fetch",
        trigger_on="finished",
      ),
    ],
  )
  habits = load_habits(tmp_path)
  watchers = habits_watching("fetch", habits)
  assert len(watchers) == 1
  assert watchers[0].name == "summarize"


def test_would_create_habit_cycle_detects_loop(tmp_path: Path) -> None:
  save_habits(
    tmp_path,
    [
      Habit(name="a", agent="self", prompt="a", trigger_after="b"),
      Habit(name="b", agent="self", prompt="b", trigger_after="a"),
    ],
  )
  habits = load_habits(tmp_path)
  # Existing a ↔ b loop — any new edge into that component is rejected.
  assert would_create_habit_cycle(habits, name="c", trigger_after="a") is True
  assert would_create_habit_cycle(habits, name="d", trigger_after="fetch") is False


def test_render_chain_prompt_substitutes_vars() -> None:
  out = render_chain_prompt(
    "Digest: {{source_report}}",
    source_habit="fetch",
    source_status="finished",
    source_report="BTC is up 2%",
  )
  assert "BTC is up 2%" in out
  assert "{{" not in out
