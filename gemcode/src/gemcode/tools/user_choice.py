"""User-choice tool: interactive (ADK) vs automatic (super mode)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
  from gemcode.config import GemCodeConfig


def make_super_get_user_choice_tool(cfg: GemCodeConfig):
  """
  Non-interactive stand-in for ADK ``get_user_choice`` when ``cfg.super_mode``.

  ADK's built-in tool is a LongRunningFunctionTool that returns ``None`` and
  waits for UI; in super mode we return the first non-empty option immediately.
  """

  def get_user_choice(options: list[str], tool_context: Any) -> str | None:
    """Pick the first option without blocking on human input (super mode)."""
    try:
      tool_context.actions.skip_summarization = True
    except Exception:
      pass
    for opt in options or []:
      s = str(opt).strip()
      if s:
        return s
    return None

  get_user_choice.__name__ = "get_user_choice"
  return get_user_choice


def append_user_choice_load_artifacts_exit_loop(
  cfg: GemCodeConfig, tools: list,
) -> None:
  """
  Append ``get_user_choice``, ``load_artifacts``, ``exit_loop`` like ADK defaults.

  When ``cfg.super_mode``, ``get_user_choice`` is a plain function that auto-picks
  the first option; otherwise the ADK LongRunningFunctionTool is used.
  """
  if getattr(cfg, "super_mode", False):
    tools.append(make_super_get_user_choice_tool(cfg))
  else:
    try:
      from google.adk.tools import get_user_choice as adk_get_user_choice

      tools.append(adk_get_user_choice)
    except Exception:
      pass
  try:
    from google.adk.tools import exit_loop, load_artifacts

    tools.extend([load_artifacts, exit_loop])
  except Exception:
    pass
