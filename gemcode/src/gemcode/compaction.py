"""Optional sliding-window trim before each model call (use with care).

Runs after `context_budget` soft char shrink when enabled. Sliding-window
dropping of whole `Content` rows can break tool-call pairing; prefer
GEMCODE_MAX_CONTEXT_CHARS + GEMCODE_TOOL_RESULT_MAX_CHARS first.
"""

from __future__ import annotations

import os

from gemcode.config import GemCodeConfig


def make_before_model_callback(cfg: GemCodeConfig):
  """
  Keep the first content block and the last N items.

  Off by default: set GEMCODE_ENABLE_COMPACT=1. Trimming can break tool-call
  pairing if misconfigured; for production prefer ADK/App compaction or
  summarization.
  """
  if os.environ.get("GEMCODE_ENABLE_COMPACT", "").lower() not in ("1", "true", "yes"):
    return None

  max_items = cfg.max_content_items

  async def before_model(callback_context, llm_request):
    contents = llm_request.contents
    if len(contents) <= max_items:
      return None
    keep_first = 1 if contents else 0
    tail = contents[-(max_items - keep_first) :]
    if keep_first:
      llm_request.contents = [contents[0], *tail]
    else:
      llm_request.contents = tail
    return None

  return before_model
