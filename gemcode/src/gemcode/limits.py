"""
Pre-model limits (cf. Claude `calculateTokenWarningState` / blocking limit checks).

Uses session state updated in `callbacks.make_after_model_callback`.
"""

from __future__ import annotations

from gemcode.config import GemCodeConfig

SESSION_TOTAL_TOKENS_KEY = "gemcode:session_total_tokens"
TOKEN_BUDGET_STOP_KEY = "gemcode:bt_token_budget_stop"
TERMINAL_REASON_KEY = "gemcode:terminal_reason"


def make_before_model_limits_callback(cfg: GemCodeConfig):
  """Block the next LLM call when cumulative session tokens exceed ceiling."""
  if cfg.max_session_tokens is None:
    return None

  async def before_model(callback_context, llm_request):
    st = callback_context.state
    total = int(st.get(SESSION_TOTAL_TOKENS_KEY, 0) or 0)
    if total >= cfg.max_session_tokens:
      from google.adk.models.llm_response import LlmResponse
      from google.genai import types

      # Record a terminal reason for stopHooks-like taxonomy.
      callback_state = callback_context.state
      if not callback_state.get(TERMINAL_REASON_KEY):
        callback_state[TERMINAL_REASON_KEY] = "session_token_limit"

      return LlmResponse(
          content=types.Content(
              role="model",
              parts=[
                  types.Part(
                      text=(
                          f"Session token ceiling ({cfg.max_session_tokens}) reached "
                          "(see GEMCODE_MAX_SESSION_TOKENS). Start a new session or raise the limit."
                      )
                  )
              ],
          ),
          turn_complete=True,
      )
    return None

  return before_model


def make_before_model_token_budget_callback(cfg: GemCodeConfig):
  """Short-circuit the next model call after token-budget stop flag."""
  if cfg.token_budget is None:
    return None

  async def before_model(callback_context, llm_request):
    st = callback_context.state
    if not st.get(TOKEN_BUDGET_STOP_KEY, False):
      return None

    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    if not st.get(TERMINAL_REASON_KEY):
      st[TERMINAL_REASON_KEY] = "token_budget_stop"

    return LlmResponse(
      content=types.Content(
        role="model",
        parts=[
          types.Part(
              text=(
                  f"Token budget ({cfg.token_budget}) exhausted for this turn. "
                  "Start a new request to continue."
              )
          )
        ],
      ),
      turn_complete=True,
    )

  return before_model
