"""
Single user turn (Claude Code: inner path ≈ `query()` invocation per message).

CLI and tests call `run_turn` with a Runner already bound to app + session service.
"""

from __future__ import annotations

from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.genai import types


async def run_turn(
    runner: Runner,
    *,
    user_id: str,
    session_id: str,
    prompt: str,
    max_llm_calls: int | None = None,
    cfg: "GemCodeConfig | None" = None,
) -> list:
  """Execute one user message; collect all Events (caller aggregates text)."""
  msg = types.Content(role="user", parts=[types.Part(text=prompt)])
  collected: list = []
  run_config = RunConfig(max_llm_calls=max_llm_calls) if max_llm_calls is not None else None
  state_delta = None
  if cfg is not None and cfg.token_budget:
    from gemcode.config import token_budget_invocation_reset

    state_delta = token_budget_invocation_reset()
  kwargs = dict(
      user_id=user_id,
      session_id=session_id,
      new_message=msg,
  )
  if run_config is not None:
    kwargs["run_config"] = run_config
  if state_delta is not None:
    kwargs["state_delta"] = state_delta
  async for event in runner.run_async(**kwargs):
    collected.append(event)
  return collected
