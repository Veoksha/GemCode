from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from google.genai import types

from gemcode.config import GemCodeConfig
from gemcode.permissions import make_before_tool_callback
from gemcode.invoke import run_turn


class ComputerUseTool:
  # Match callbacks._is_computer_use_tool() heuristics.
  __module__ = "google.adk.tools.computer_use.computer_use_tool"

  def __init__(self, name: str = "click_at"):
    self.name = name


def test_before_tool_requests_confirmation_for_mutation(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.permission_mode = "default"
  cfg.yes_to_all = False
  cfg.interactive_permission_ask = True

  cb = make_before_tool_callback(cfg)

  tool = MagicMock()
  tool.name = "write_file"

  tool_context = MagicMock()
  tool_context.state = {}

  out = cb(tool, {"path": "x", "content": "y"}, tool_context)
  assert out is not None
  assert out.get("error_kind") == "permission_block"
  tool_context.request_confirmation.assert_called_once()
  hint = tool_context.request_confirmation.call_args.kwargs.get("hint", "")
  assert "write_file" in hint or "mutation" in hint.lower()


def test_before_tool_requests_confirmation_for_computer_use(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.permission_mode = "default"
  cfg.yes_to_all = False
  cfg.interactive_permission_ask = True

  cb = make_before_tool_callback(cfg)

  tool = ComputerUseTool("click_at")
  tool_context = MagicMock()
  tool_context.state = {}

  out = cb(tool, {"x": 1, "y": 2}, tool_context)
  assert out is not None
  assert out.get("error_kind") == "permission_block"
  tool_context.request_confirmation.assert_called_once()
  hint = tool_context.request_confirmation.call_args.kwargs.get("hint", "")
  assert "browser automation" in hint.lower()


class _FakeRunner:
  def __init__(self, *, confirmation_fc: types.FunctionCall):
    self._confirmation_fc = confirmation_fc
    self.call_index = 0
    self.second_new_message: types.Content | None = None

  async def run_async(self, **kwargs):
    self.call_index += 1
    new_message: types.Content = kwargs["new_message"]

    if self.call_index == 1:
      # First invocation: emit a tool confirmation request.
      event = MagicMock()
      event.get_function_calls.return_value = [self._confirmation_fc]
      yield event
      return

    # Second invocation: caller should provide a FunctionResponse for the
    # confirmation function call.
    self.second_new_message = new_message
    event = MagicMock()
    event.get_function_calls.return_value = []
    yield event


def test_run_turn_sends_function_response_confirmation(tmp_path: Path, monkeypatch):
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.interactive_permission_ask = True
  cfg.yes_to_all = False

  # Ensure interactive_enabled() becomes true.
  monkeypatch.setattr(cfg, "interactive_permission_ask", True)
  monkeypatch.setattr(
    __import__("sys").stdin, "isatty", lambda: True, raising=False
  )

  # Simulate user approving the confirmation.
  monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: "y")

  confirmation_fc = types.FunctionCall(
    id="confirm_fc_1",
    name="adk_request_confirmation",
    args={
      "originalFunctionCall": {"id": "tool_fc_1", "name": "write_file", "args": {"path": "x"}},
      "toolConfirmation": {"hint": "mutate filesystem"},
    },
  )
  runner = _FakeRunner(confirmation_fc=confirmation_fc)

  asyncio.run(
    run_turn(
      runner,
      user_id="u",
      session_id="s",
      prompt="hello",
      cfg=cfg,
    )
  )

  assert runner.second_new_message is not None
  parts = runner.second_new_message.parts or []
  assert len(parts) == 1
  part = parts[0]
  assert part.function_response is not None
  assert part.function_response.name == "adk_request_confirmation"
  assert part.function_response.id == "confirm_fc_1"
  assert part.function_response.response == {"confirmed": True}

