"""Record one-shot shell allowlist bypass after interactive user approval."""

from __future__ import annotations

import contextvars

# Basename of executable (e.g. "rm") cleared right after run_command consumes it.
_shell_confirmed_basename: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "gemcode_shell_confirmed_basename",
    default=None,
)


def arm_confirmed_shell_basename(basename: str) -> None:
  """ADK before_tool calls this when the user confirmed the pending run_command."""
  _shell_confirmed_basename.set(basename)


def consume_confirmed_shell_if_matches(exe_basename: str) -> bool:
  """
  Clears the one-shot gate only when exe_basename equals the armed name.
  Avoids burning the token on a different command if the model mis-orders calls.
  """
  v = _shell_confirmed_basename.get()
  if v is None or v != exe_basename:
    return False
  _shell_confirmed_basename.set(None)
  return True
