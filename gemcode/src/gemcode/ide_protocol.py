"""
GemCode IDE stdio protocol (JSON Lines).

This module defines the *wire format* for `gemcode ide --stdio`, used by IDE
extensions (VS Code) to talk to a long-lived GemCode engine process.

Design goals:
- Human-readable JSONL (easy to debug)
- Streaming (token deltas + progress)
- Safe editing (engine proposes; IDE applies via WorkspaceEdit)
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Any


PROTOCOL_VERSION = 1


def _now_ms() -> int:
  return int(time.time() * 1000)


def dumps(obj: Any) -> str:
  return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


@dataclass
class IdeEmitter:
  """Writes JSONL messages to stdout (flushes each line)."""

  stream: Any = sys.stdout

  def send(self, msg: dict) -> None:
    msg = dict(msg or {})
    msg.setdefault("v", PROTOCOL_VERSION)
    msg.setdefault("ts_ms", _now_ms())
    self.stream.write(dumps(msg) + "\n")
    try:
      self.stream.flush()
    except Exception:
      pass


def parse_json_line(line: str) -> dict[str, Any]:
  try:
    obj = json.loads(line)
  except Exception as e:
    return {"type": "invalid", "error": f"invalid_json: {e}"}
  if not isinstance(obj, dict):
    return {"type": "invalid", "error": "message must be a JSON object"}
  return obj

