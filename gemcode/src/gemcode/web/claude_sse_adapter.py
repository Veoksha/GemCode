"""
Backward-compatibility wrapper.

Older web integration layers imported `gemcode.web.claude_sse_adapter`.
The implementation moved to `gemcode.web.sse_adapter` to avoid vendor-specific naming.
"""

from __future__ import annotations

from gemcode.web.sse_adapter import (  # noqa: F401
  extract_text_from_event as _extract_text_from_event,
  run_adapter,
)


def main() -> None:
  import json
  import sys
  from asyncio import run

  req = json.loads(sys.stdin.read() or "{}")
  run(run_adapter(req))


if __name__ == "__main__":
  main()

