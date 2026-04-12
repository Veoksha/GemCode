"""
GemCode IDE stdio protocol (JSON Lines).

This module defines the *wire format* for `gemcode ide --stdio`, used by IDE
extensions (VS Code) to talk to a long-lived GemCode engine process.

Design goals:
- Human-readable JSONL (easy to debug)
- Streaming (token deltas + progress)
- Safe editing (engine proposes; IDE applies via WorkspaceEdit)

Attachments on ``action: turn`` may include:

- Textual: ``type: selection`` / ``file`` (appended into the prompt as fenced blocks).
- Binary / multimodal: ``type: inline`` | ``binary`` | ``blob`` with ``data`` or ``base64``
  (standard base64), plus optional ``filename`` / ``name`` and ``mimeType`` / ``mime_type``.
  The engine writes bytes to a temp file and passes them to Gemini as inline parts
  (same limits as CLI ``--attach``).
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Any


PROTOCOL_VERSION = 2


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


def make_event(*, event: str, **payload: Any) -> dict[str, Any]:
  msg: dict[str, Any] = {"type": "event", "event": event}
  msg.update(payload)
  return msg


def make_response(*, id: str, ok: bool, **payload: Any) -> dict[str, Any]:
  msg: dict[str, Any] = {"type": "response", "id": id, "ok": bool(ok)}
  msg.update(payload)
  return msg


