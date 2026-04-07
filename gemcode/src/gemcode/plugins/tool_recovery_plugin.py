"""
familiar recovery-loop for GemCode tool failures.

We complement ADK's `ReflectAndRetryToolPlugin` by treating our tool result
dicts like `{"error": "...", "error_kind": "..."}` as retryable tool failures
so the model gets reflection guidance and can try a corrected approach.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from google.adk.plugins.reflect_retry_tool_plugin import (
    ReflectAndRetryToolPlugin,
    TrackingScope,
)
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from gemcode.audit import append_audit
from gemcode.config import GemCodeConfig


_STATE_FAILURE_KEY = "gemcode:consecutive_tool_failures"
_TERMINAL_REASON_KEY = "gemcode:terminal_reason"

_ERROR_KIND_PERMISSION_DENIED = "permission_denied"
_ERROR_KIND_PERMISSION_BLOCK = "permission_block"
_ERROR_KIND_CIRCUIT_BREAKER = "circuit_breaker"


class GemCodeReflectAndRetryToolPlugin(ReflectAndRetryToolPlugin):
  """Retry tool failures with reflection guidance (recovery loop)."""

  def __init__(self, cfg: GemCodeConfig):
    self.cfg = cfg

    enabled = os.environ.get("GEMCODE_ENABLE_TOOL_RECOVERY_RETRY", "1").lower()
    if enabled not in ("1", "true", "yes", "on"):
      # Still construct; we just set max_retries=0 so it becomes inert.
      max_retries = 0
    else:
      max_retries = int(os.environ.get("GEMCODE_TOOL_REFLECT_MAX_RETRIES", "1"))

    super().__init__(
        max_retries=max_retries,
        throw_exception_if_retry_exceeded=False,
        tracking_scope=TrackingScope.INVOCATION,
    )

  async def extract_error_from_result(
    self,
    *,
    tool: BaseTool,
    tool_args: dict[str, Any],
    tool_context: ToolContext,
    result: Any,
  ) -> Optional[Exception]:
    """
    Treat `{ "error": ... }` tool results as retryable failures.

    Important: skip policy rejections (permission denials / circuit breaker)
    so we don't waste retries on user-actionable gating.
    """
    if not isinstance(result, dict):
      return None
    if "error" not in result:
      return None

    err_kind = result.get("error_kind")
    if err_kind in (
      _ERROR_KIND_PERMISSION_DENIED,
      _ERROR_KIND_PERMISSION_BLOCK,
      _ERROR_KIND_CIRCUIT_BREAKER,
    ):
      return None

    # Update GemCode streak/terminal state since canonical agent callbacks
    # are likely short-circuited when this plugin returns a reflection.
    try:
      st = tool_context.state
      st[_STATE_FAILURE_KEY] = st.get(_STATE_FAILURE_KEY, 0) + 1
      if not st.get(_TERMINAL_REASON_KEY):
        st[_TERMINAL_REASON_KEY] = "tool_retryable_error"
    except Exception:
      pass

    err = result.get("error")
    err_text = err if isinstance(err, str) else str(err)
    append_audit(
        self.cfg.project_root,
        {
          "phase": "tool_recovery_retry",
          "tool": tool.name,
          "error_kind": err_kind,
          "error": err_text,
        },
    )

    return Exception(err_text)

  async def on_tool_error_callback(
    self,
    *,
    tool: BaseTool,
    tool_args: dict[str, Any],
    tool_context: ToolContext,
    error: Exception,
  ) -> Optional[dict[str, Any]]:
    """Ensure GemCode streak/terminal state is updated on exceptions."""
    try:
      st = tool_context.state
      st[_STATE_FAILURE_KEY] = st.get(_STATE_FAILURE_KEY, 0) + 1
      if not st.get(_TERMINAL_REASON_KEY):
        st[_TERMINAL_REASON_KEY] = "tool_exception"
    except Exception:
      pass

    append_audit(
        self.cfg.project_root,
        {
          "phase": "tool_recovery_exception",
          "tool": tool.name,
          "error": f"{type(error).__name__}: {error}",
        },
    )

    return await super().on_tool_error_callback(
      tool=tool,
      tool_args=tool_args,
      tool_context=tool_context,
      error=error,
    )

