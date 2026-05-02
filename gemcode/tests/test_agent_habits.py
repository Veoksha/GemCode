"""Tests for the agent habits (scheduled recurring tasks) system."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from gemcode.agent_habits import (
  Habit,
  HabitScheduler,
  _is_due,
  load_habits,
  make_habits_tools,
  save_habits,
)
from gemcode.config import GemCodeConfig


def test_save_and_load_habits(tmp_path: Path) -> None:
  habits = [
    Habit(name="test-watch", agent="kaira", prompt="Run tests", every_seconds=300),
    Habit(name="nightly", agent="verifier", prompt="Audit", daily_at="02:00"),
  ]
  save_habits(tmp_path, habits)
  loaded = load_habits(tmp_path)
  assert len(loaded) == 2
  assert loaded[0].name == "test-watch"
  assert loaded[0].every_seconds == 300
  assert loaded[1].daily_at == "02:00"


def test_is_due_interval_first_run() -> None:
  h = Habit(name="x", agent="a", prompt="p", every_seconds=60, last_run_ms=0)
  assert _is_due(h, time.time()) is True


def test_is_due_interval_not_yet() -> None:
  now = time.time()
  h = Habit(name="x", agent="a", prompt="p", every_seconds=60, last_run_ms=int(now * 1000))
  assert _is_due(h, now + 30) is False  # Only 30s passed, need 60


def test_is_due_interval_ready() -> None:
  now = time.time()
  h = Habit(name="x", agent="a", prompt="p", every_seconds=60, last_run_ms=int((now - 61) * 1000))
  assert _is_due(h, now) is True


def test_is_due_disabled() -> None:
  h = Habit(name="x", agent="a", prompt="p", every_seconds=10, enabled=False)
  assert _is_due(h, time.time()) is False


def test_is_due_max_runs_reached() -> None:
  h = Habit(name="x", agent="a", prompt="p", every_seconds=10, max_runs=5, run_count=5)
  assert _is_due(h, time.time()) is False


def test_habits_tools_add_and_list(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  tools = make_habits_tools(cfg)
  add = next(t for t in tools if getattr(t, "__name__", "") == "habits_add")
  lst = next(t for t in tools if getattr(t, "__name__", "") == "habits_list")

  result = add("my-habit", "kaira", "do stuff", every_minutes=15)
  assert result["ok"] is True
  assert result["name"] == "my-habit"

  listed = lst()
  assert listed["count"] == 1
  assert listed["habits"][0]["name"] == "my-habit"
  assert listed["habits"][0]["every_seconds"] == 900


def test_habits_tools_pause_resume(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  tools = make_habits_tools(cfg)
  add = next(t for t in tools if getattr(t, "__name__", "") == "habits_add")
  pause = next(t for t in tools if getattr(t, "__name__", "") == "habits_pause")
  resume = next(t for t in tools if getattr(t, "__name__", "") == "habits_resume")

  add("test-h", "kaira", "test", every_seconds=60)

  r = pause("test-h")
  assert r["ok"] is True
  assert r["enabled"] is False

  habits = load_habits(tmp_path)
  assert habits[0].enabled is False

  r = resume("test-h")
  assert r["ok"] is True
  assert r["enabled"] is True


def test_habits_tools_remove(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  tools = make_habits_tools(cfg)
  add = next(t for t in tools if getattr(t, "__name__", "") == "habits_add")
  remove = next(t for t in tools if getattr(t, "__name__", "") == "habits_remove")

  add("h1", "kaira", "task1", every_seconds=30)
  add("h2", "verifier", "task2", every_seconds=60)

  r = remove("h1")
  assert r["ok"] is True
  assert r["removed"] == 1

  habits = load_habits(tmp_path)
  assert len(habits) == 1
  assert habits[0].name == "h2"
