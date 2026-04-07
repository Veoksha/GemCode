"""
ADK plugin: complements Claude-like stopHooks with GemCode terminal reasons,
optional memory ingestion, and post-turn hook execution.
"""

from __future__ import annotations

from typing import Any

import os

from google.adk.plugins.base_plugin import BasePlugin

from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from gemcode.config import GemCodeConfig
from gemcode.query.stop_hooks import run_post_turn_hooks
from gemcode.audit import append_audit
from gemcode.prompt_suggestions import build_prompt_suggestion


class GemCodeTerminalHooksPlugin(BasePlugin):
  def __init__(self, cfg: GemCodeConfig):
    super().__init__(name="gemcode_terminal_hooks")
    self.cfg = cfg

  def _use_interactions_for_prompt_suggestions(self) -> bool:
    v = os.environ.get("GEMCODE_PROMPT_SUGGESTIONS_USE_INTERACTIONS", "1")
    return v.lower() in ("1", "true", "yes", "on")

  def _find_previous_interaction_id(
    self, *, callback_context: Any, agent_name: str
  ) -> str | None:
    # Interactions chaining uses `previous_interaction_id` extracted from the
    # most recent model response event for the same agent.
    try:
      events = callback_context.session.events or []
    except Exception:
      return None
    for ev in reversed(events):
      if getattr(ev, "author", None) == agent_name and getattr(
        ev, "interaction_id", None
      ):
        return ev.interaction_id
    return None

  async def _suggest_via_interactions(
    self,
    *,
    terminal_reason: str,
    callback_context: Any,
    agent: Any,
    heuristic: str,
  ) -> str | None:
    if not self._use_interactions_for_prompt_suggestions():
      return None

    try:
      previous_interaction_id = self._find_previous_interaction_id(
        callback_context=callback_context,
        agent_name=getattr(agent, "name", "gemcode"),
      )

      prompt = (
        "You are GemCode. Provide the best next-step guidance for the user. "
        "Terminal reason: {reason}. "
        "Write a single short sentence (<= 220 chars). "
        "If it involves policy/actions, reference exact flags like `--yes` or `--session`.\n\n"
        "Heuristic suggestion (may be imperfect): {heuristic}"
      ).format(reason=terminal_reason, heuristic=heuristic)

      llm = Gemini(model=self.cfg.model, use_interactions_api=True)
      req = LlmRequest(
        model=self.cfg.model,
        contents=[
          types.Content(
            role="user",
            parts=[types.Part(text=prompt)],
          )
        ],
        config=types.GenerateContentConfig(),
      )
      if previous_interaction_id:
        req.previous_interaction_id = previous_interaction_id

      async for resp in llm.generate_content_async(req, stream=False):
        if resp.content and resp.content.parts:
          texts = [
            getattr(p, "text", None)
            for p in resp.content.parts
            if getattr(p, "text", None)
          ]
          if texts:
            out = "".join(texts).strip()
            if out:
              return out
      return None
    except Exception as e:
      append_audit(
        self.cfg.project_root,
        {
          "phase": "prompt_suggestion_interactions",
          "ok": False,
          "error": str(e),
          "terminal_reason": terminal_reason,
        },
      )
      return None

  async def after_agent_callback(self, *, agent: Any, callback_context: Any):
    # callback_context is an ADK Context (mutable state + helper methods).
    state = callback_context.state
    terminal_reason = state.get("gemcode:terminal_reason", None)
    if not terminal_reason:
      terminal_reason = "completed"

    append_audit(self.cfg.project_root, {"phase": "terminal", "reason": terminal_reason})

    heuristic = build_prompt_suggestion(
      self.cfg, terminal_reason=terminal_reason
    )
    if heuristic:
      suggestion = heuristic
      suggestion_via_interactions = await self._suggest_via_interactions(
        terminal_reason=terminal_reason,
        callback_context=callback_context,
        agent=agent,
        heuristic=heuristic,
      )
      if suggestion_via_interactions:
        suggestion = suggestion_via_interactions

      append_audit(
        self.cfg.project_root,
        {
          "phase": "prompt_suggestion",
          "terminal_reason": terminal_reason,
          "suggestion": suggestion,
        },
      )

      # Surface suggestion to the TUI by storing it on cfg.
      # The TUI reads cfg._last_prompt_suggestion after each turn and displays it.
      try:
        object.__setattr__(self.cfg, "_last_prompt_suggestion", suggestion)
      except Exception:
        pass
    else:
      # Clear any stale suggestion from the previous turn.
      try:
        object.__setattr__(self.cfg, "_last_prompt_suggestion", None)
      except Exception:
        pass

    if getattr(self.cfg, "enable_memory", False):
      try:
        await callback_context.add_session_to_memory()
        append_audit(self.cfg.project_root, {"phase": "memory_ingest", "ok": True})
      except Exception as e:
        append_audit(
          self.cfg.project_root,
          {"phase": "memory_ingest", "ok": False, "error": str(e)},
        )

    # Hermes-style evolving: run a bounded, cheap post-turn learner that writes
    # durable insights to curated memory / notes (opt-in).
    if getattr(self.cfg, "enable_background_learner", False):
      try:
        from gemcode.learning import run_background_learner
        await run_background_learner(cfg=self.cfg, callback_context=callback_context)
      except Exception as e:
        append_audit(
          self.cfg.project_root,
          {"phase": "background_learner", "ok": False, "error": str(e)},
        )

    # Execute stopHooks-like script hook at the end of the invocation.
    try:
      run_post_turn_hooks(
        self.cfg,
        session_id=callback_context.session.id,
        user_id=callback_context.user_id,
      )
    except Exception as e:
      append_audit(
        self.cfg.project_root,
        {"phase": "post_turn_hook", "ok": False, "error": str(e)},
      )

    return None

