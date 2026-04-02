"""Tests for context_budget (tool truncation + pre-model text shrink)."""

from pathlib import Path

from google.genai import types

from gemcode.config import GemCodeConfig
from gemcode.context_budget import (
  estimate_contents_text_chars,
  make_before_model_context_shrink_callback,
  shrink_contents_text_inplace,
  truncate_tool_result_dict,
)


def test_truncate_tool_result_dict_stdout() -> None:
  d = {"stdout": "x" * 100, "exit_code": 0}
  out, changed = truncate_tool_result_dict(d, 50)
  assert changed
  assert len(out["stdout"]) < len(d["stdout"])
  assert "truncated" in out["stdout"]


def test_truncate_tool_result_dict_matches_list() -> None:
  d = {"matches": ["a" * 80, "b" * 80]}
  out, changed = truncate_tool_result_dict(d, 50)
  assert changed
  assert "truncated" in out["matches"][0]
  assert "truncated" in out["matches"][1]


def test_truncate_tool_result_dict_unchanged() -> None:
  d = {"stdout": "short"}
  out, changed = truncate_tool_result_dict(d, 500)
  assert not changed
  assert out == d


def test_shrink_contents_oldest_first() -> None:
  p_old = types.Part(text="A" * 5000)
  p_new = types.Part(text="B" * 5000)
  contents = [
      types.Content(role="user", parts=[p_old]),
      types.Content(role="user", parts=[p_new]),
  ]
  assert estimate_contents_text_chars(contents) == 10_000
  shrink_contents_text_inplace(contents, 6000)
  assert estimate_contents_text_chars(contents) <= 6000
  # Oldest trimmed more aggressively than newest when both were large.
  assert len(p_old.text) < len(p_new.text)


def test_make_before_model_context_shrink_disabled() -> None:
  cfg = GemCodeConfig(project_root=Path("."), context_shrink_enabled=False)
  assert make_before_model_context_shrink_callback(cfg) is None


def test_make_before_model_context_shrink_zero_budget() -> None:
  cfg = GemCodeConfig(project_root=Path("."), max_context_chars=0)
  assert make_before_model_context_shrink_callback(cfg) is None
