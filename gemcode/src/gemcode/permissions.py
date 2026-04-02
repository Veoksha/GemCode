"""Backward-compatible re-exports. Prefer `gemcode.callbacks` for new code."""

from gemcode.callbacks import make_before_tool_callback

__all__ = ["make_before_tool_callback"]
