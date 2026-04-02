"""Tests for shared REPL slash dispatcher."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gemcode.config import GemCodeConfig
from gemcode.repl_slash import process_repl_slash


@pytest.mark.asyncio
async def test_process_repl_slash_none_for_plain_prompt() -> None:
  cfg = GemCodeConfig(project_root=Path("."))
  assert (
      await process_repl_slash(
          cfg=cfg,
          runner=object(),
          session_id="s",
          prompt_text="hello world",
      )
      is None
  )


@pytest.mark.asyncio
async def test_process_repl_slash_help() -> None:
  cfg = GemCodeConfig(project_root=Path("."))
  buf: list[str] = []

  def capture(*a: object, **_: object) -> None:
    if a:
      buf.append(str(a[0]))

  res = await process_repl_slash(
      cfg=cfg,
      runner=object(),
      session_id="s",
      prompt_text="/help",
      print_fn=capture,
  )
  assert res is not None
  assert res.skip_model_turn is True
  joined = "\n".join(buf)
  assert "Slash commands" in joined


@pytest.mark.asyncio
async def test_process_repl_slash_context_uses_session() -> None:
  cfg = GemCodeConfig(project_root=Path("."))
  sess = MagicMock()
  sess.state = {"gemcode:last_prompt_tokens": 100}
  runner = MagicMock()
  runner.session_service.get_session = AsyncMock(return_value=sess)

  res = await process_repl_slash(
      cfg=cfg,
      runner=runner,
      session_id="sid",
      prompt_text="/context",
  )
  assert res is not None
  assert res.skip_model_turn is True
  runner.session_service.get_session.assert_called_once()


@pytest.mark.asyncio
async def test_process_repl_slash_tools_passes_extra_tools() -> None:
  cfg = GemCodeConfig(project_root=Path("."))
  dummy = object()
  with patch("gemcode.tools_inspector.inspect_tools") as mock_inspect:
    mock_inspect.return_value = []
    res = await process_repl_slash(
        cfg=cfg,
        runner=object(),
        session_id="s",
        prompt_text="/tools",
        extra_tools=[dummy],
    )
  assert res is not None
  assert res.skip_model_turn is True
  mock_inspect.assert_called_once()
  assert mock_inspect.call_args[0][0] is cfg
  assert mock_inspect.call_args.kwargs.get("extra_tools") == [dummy]
