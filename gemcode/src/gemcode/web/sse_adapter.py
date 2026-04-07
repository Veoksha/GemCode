from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from gemcode.config import GemCodeConfig
from gemcode.session_runtime import create_runner


def extract_text_from_event(event: Any) -> str:
  """
  Best-effort extraction of assistant-visible text from ADK events.

  The CLI uses `event.content.parts` and skips events whose author is "user".
  The web UI reuses the same heuristic so it can render incremental text deltas.
  """
  try:
    content = getattr(event, "content", None)
    author = getattr(event, "author", None)
    if author == "user":
      return ""
    if not content or not getattr(content, "parts", None):
      return ""
    out: list[str] = []
    for p in content.parts:
      t = getattr(p, "text", None)
      if isinstance(t, str) and t:
        out.append(t)
    return "".join(out)
  except Exception:
    return ""


def _extract_text_from_message_content(content: Any) -> str:
  if isinstance(content, str):
    return content
  try:
    return json.dumps(content, ensure_ascii=False)
  except Exception:
    return str(content)


def _build_prompt(messages: list[dict[str, Any]]) -> str:
  """
  The web UI sends conversation history in `messages`.

  GemCode's current invocation is "single user message" per call, so we embed
  the conversation into the prompt text.
  """
  lines: list[str] = []
  for m in messages:
    role = m.get("role")
    content = _extract_text_from_message_content(m.get("content"))
    if role == "user":
      lines.append(f"User: {content}")
    elif role == "assistant":
      lines.append(f"Assistant: {content}")
  if not lines:
    return ""
  return "Conversation so far:\n" + "\n".join(lines) + "\n\nNow respond as the assistant."


def _sse_emit(obj: dict[str, Any]) -> None:
  sys.stdout.write(f"data: {json.dumps(obj)}\n\n")
  sys.stdout.flush()


def _iter_chunks(text: str, chunk_size: int) -> Iterable[str]:
  if chunk_size <= 0:
    yield text
    return
  for i in range(0, len(text), chunk_size):
    yield text[i : i + chunk_size]


async def _emit_text_delta(index: int, delta: str) -> None:
  if not delta:
    return
  chunk_size = int(os.environ.get("GEMCODE_WEB_STREAM_CHUNK", "8"))
  for piece in _iter_chunks(delta, max(1, chunk_size)):
    _sse_emit(
      {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "text_delta", "text": piece},
      }
    )
    _sse_emit({"type": "text", "content": piece})
    await asyncio.sleep(0)


async def run_adapter(req: dict[str, Any]) -> None:
  # ---- Request parsing ----
  messages = req.get("messages")
  requested_model = req.get("model")
  model = requested_model or os.environ.get("GEMCODE_MODEL") or "gemini-2.5-flash"

  if not isinstance(messages, list):
    raise ValueError("messages must be a list")

  prompt = _build_prompt(messages)

  # ---- Config ----
  project_root = os.environ.get("GEMCODE_WEB_PROJECT_ROOT") or os.getcwd()
  cfg = GemCodeConfig(project_root=Path(project_root))

  # Permission mapping: for the web MVP we gate all mutations behind `--yes`
  # style confirmation using an env flag.
  cfg.permission_mode = os.environ.get("GEMCODE_PERMISSION_MODE", cfg.permission_mode)
  cfg.yes_to_all = os.environ.get("GEMCODE_WEB_YES_TO_ALL", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
  )

  # Model mapping/validation:
  # The ported web UI may send unknown model ids; ignore those and fall back.
  MODEL_MAP: dict[str, str] = {
    "gemcode-pro": "gemini-2.5-pro",
    "gemcode-balanced": "gemini-2.5-flash",
    "gemcode-fast": "gemini-2.5-flash",
  }

  resolved_model: str | None = None
  if isinstance(requested_model, str) and requested_model.strip():
    rm = requested_model.strip()
    if rm in MODEL_MAP:
      resolved_model = MODEL_MAP[rm]
    elif rm.startswith("gemini") or rm.startswith("models/"):
      resolved_model = rm

  if resolved_model:
    cfg.model = resolved_model
    cfg.model_overridden = True
    model = resolved_model

  # ---- Session + runner ----
  session_id = req.get("session_id") or str(uuid.uuid4())

  # ---- SSE event mapping (text-only MVP) ----
  message_id = f"msg_{uuid.uuid4().hex[:12]}"
  assistant_block_index = 0

  _sse_emit(
    {
      "type": "message_start",
      "message": {
        "id": message_id,
        "role": "assistant",
        "model": model,
        "usage": {"input_tokens": 0, "output_tokens": 0},
      },
    }
  )
  _sse_emit(
    {
      "type": "content_block_start",
      "index": assistant_block_index,
      "content_block": {"type": "text", "text": ""},
    }
  )

  emitted_text = ""
  runner = None
  mocked = False
  try:
    # ---- Mock mode (for web smoke tests / local dev without API keys) ----
    mock_response = os.environ.get("GEMCODE_WEB_MOCK_RESPONSE")
    if isinstance(mock_response, str) and mock_response.strip():
      mocked = True
      full = mock_response
      chunk_size = int(os.environ.get("GEMCODE_WEB_MOCK_CHUNK", "6"))
      for i in range(0, len(full), max(1, chunk_size)):
        delta = full[i : i + chunk_size]
        emitted_text += delta
        _sse_emit(
          {
            "type": "content_block_delta",
            "index": assistant_block_index,
            "delta": {"type": "text_delta", "text": delta},
          }
        )
        _sse_emit({"type": "text", "content": delta})
        await asyncio.sleep(0.01)
      # Skip real runner; fall through to stop/done frames.
    else:
      # ---- Real ADK streaming mode ----
      runner = create_runner(cfg, extra_tools=None)

      from google.adk.agents.run_config import RunConfig
      from google.genai import types

      new_message = types.Content(role="user", parts=[types.Part(text=prompt)])
      run_config = (
        RunConfig(max_llm_calls=cfg.max_llm_calls) if cfg.max_llm_calls is not None else None
      )

      async for event in runner.run_async(
        user_id=req.get("user_id") or "web",
        session_id=session_id,
        new_message=new_message,
        **({"run_config": run_config} if run_config is not None else {}),
      ):
        text = extract_text_from_event(event)
        if not text:
          continue

        if text.startswith(emitted_text):
          delta = text[len(emitted_text) :]
        else:
          common = 0
          max_common = min(len(text), len(emitted_text))
          while common < max_common and text[common] == emitted_text[common]:
            common += 1
          delta = text[common:]

        if delta:
          emitted_text += delta
          await _emit_text_delta(assistant_block_index, delta)
  finally:
    if runner is not None:
      await runner.close()

  _sse_emit({"type": "content_block_stop", "index": assistant_block_index})
  _sse_emit({"type": "message_stop"})
  _sse_emit({"type": "done"})


def main() -> None:
  import json
  import sys
  from asyncio import run

  req = json.loads(sys.stdin.read() or "{}")
  run(run_adapter(req))


if __name__ == "__main__":
  main()

