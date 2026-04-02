"""
Outer session engine (cf. claude-code `QueryEngine.ts`).

Owns config + runner + one `submit_message` path per user turn.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from google.adk.runners import Runner

from gemcode.config import GemCodeConfig
from gemcode.invoke import run_turn
from gemcode.session_runtime import create_runner


class GemCodeQueryEngine:
  """One engine per workspace session; reuse runner for multiple turns."""

  def __init__(
      self,
      cfg: GemCodeConfig,
      *,
      extra_tools: list | None = None,
      runner_factory: Callable[[GemCodeConfig, list | None], Runner] | None = None,
  ) -> None:
    self.cfg = cfg
    self._extra_tools = extra_tools
    self._runner_factory = runner_factory or create_runner
    self._runner: Runner | None = None

  @property
  def runner(self) -> Runner:
    if self._runner is None:
      self._runner = self._runner_factory(self.cfg, self._extra_tools)
    return self._runner

  async def submit_message(
      self,
      prompt: str,
      *,
      user_id: str = "local",
      session_id: str,
  ) -> list[Any]:
    """Run one user message; returns collected ADK events."""
    return await run_turn(
        self.runner,
        user_id=user_id,
        session_id=session_id,
        prompt=prompt,
        max_llm_calls=self.cfg.max_llm_calls,
        cfg=self.cfg,
    )
