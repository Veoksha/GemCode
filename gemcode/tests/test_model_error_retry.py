"""Unit tests for invoke retry decision helpers."""

from __future__ import annotations

from dataclasses import dataclass

from gemcode.invoke import _events_to_text, _is_retryable_context_model_error


@dataclass
class _FakePart:
  text: str


@dataclass
class _FakeContent:
  parts: list[_FakePart]


@dataclass
class _FakeEvent:
  author: str
  content: _FakeContent

  def get_function_calls(self):
    return []


def test_is_retryable_context_model_error_true() -> None:
  text = (
    "Request may be too large — try GEMCODE_MAX_CONTEXT_CHARS / "
    "GEMCODE_TOOL_RESULT_MAX_CHARS, start a new session."
  )
  assert _is_retryable_context_model_error(text)


def test_is_retryable_context_model_error_false_for_generic() -> None:
  assert not _is_retryable_context_model_error("RuntimeError: something broke")


def test_events_to_text_ignores_user_author() -> None:
  events = [
    _FakeEvent(
      author="user",
      content=_FakeContent(parts=[_FakePart(text="USER_ONLY_TEXT")]),
    ),
    _FakeEvent(
      author="gemcode",
      content=_FakeContent(parts=[_FakePart(text="Request may be too large")]),
    ),
  ]
  t = _events_to_text(events)
  assert "Request may be too large" in t
  assert "USER_ONLY_TEXT" not in t

