"""
Tune third-party loggers for interactive CLI/TUI (expected Gemini function-call noise).
"""

from __future__ import annotations

import logging
import os


def apply_gemcode_logging_filters() -> None:
  """
  google.genai logs logger.warning when .text strips non-text parts (normal with tools).

  That uses the **logging** module, not warnings.warn — filterwarnings cannot silence it.
  Set GEMCODE_VERBOSE_GENAI=1 to keep those lines.
  """
  if os.environ.get("GEMCODE_VERBOSE_GENAI", "").lower() in (
      "1",
      "true",
      "yes",
      "on",
  ):
    return

  class _Filter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
      try:
        msg = record.getMessage()
      except Exception:
        return True
      if "non-text parts in the response" in msg:
        return False
      if "multiple candidates in the response" in msg:
        return False
      return True

  f = _Filter()
  for name in (
      "google_genai.types",
      "google_genai",
      "google.genai.types",
  ):
    logging.getLogger(name).addFilter(f)
