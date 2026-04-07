"""
Compatibility shim: re-exports the SSE web adapter for integrations that used
the older module path. Implementation lives in `gemcode.web.sse_adapter`.
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
