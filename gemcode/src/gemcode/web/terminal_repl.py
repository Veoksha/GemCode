from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig
from gemcode.session_runtime import create_runner
from gemcode.web.sse_adapter import extract_text_from_event


def _truthy_env(name: str, *, default: bool = False) -> bool:
  v = os.environ.get(name)
  if v is None:
    return default
  return v.lower() in ("1", "true", "yes", "on")


async def run_single_turn(
  *,
  runner: Any,
  cfg: GemCodeConfig,
  prompt: str,
  user_id: str,
  session_id: str,
) -> None:
  """
  Run one user line through GemCode's ADK runner, streaming incremental
  assistant-visible text to stdout (so the browser can render it as PTY
  output bytes).
  """
  # Import here to avoid module import overhead / side effects at startup.
  from google.adk.agents.run_config import RunConfig
  from google.genai import types

  emitted_text = ""

  new_message = types.Content(role="user", parts=[types.Part(text=prompt)])
  run_config = (
    RunConfig(max_llm_calls=cfg.max_llm_calls)
    if cfg.max_llm_calls is not None
    else None
  )

  kwargs: dict[str, Any] = {
    "user_id": user_id,
    "session_id": session_id,
    "new_message": new_message,
  }
  if run_config is not None:
    kwargs["run_config"] = run_config

  async for event in runner.run_async(**kwargs):
    text = extract_text_from_event(event)
    if not text:
      continue

    # Match the delta logic from the SSE adapter so the UI can
    # render incremental output without repeating content.
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
      sys.stdout.write(delta)
      sys.stdout.flush()


async def run_mock_turn(prompt: str) -> None:
  mock_response = os.environ.get("GEMCODE_WEB_MOCK_RESPONSE")
  if not isinstance(mock_response, str) or not mock_response.strip():
    return

  chunk_size = int(os.environ.get("GEMCODE_WEB_MOCK_CHUNK", "6"))
  # Provide a tiny delay to make streaming observable in the terminal.
  full = mock_response
  for i in range(0, len(full), max(1, chunk_size)):
    delta = full[i : i + chunk_size]
    sys.stdout.write(delta)
    sys.stdout.flush()
    await asyncio.sleep(0.01)


async def repl() -> None:
  project_root = os.environ.get("GEMCODE_WEB_PROJECT_ROOT") or os.getcwd()

  cfg = GemCodeConfig(project_root=Path(project_root))
  cfg.permission_mode = os.environ.get("GEMCODE_PERMISSION_MODE", cfg.permission_mode)
  cfg.yes_to_all = _truthy_env("GEMCODE_WEB_YES_TO_ALL", default=False)
  # Avoid interactive HITL prompts inside the terminal PTY.
  cfg.interactive_permission_ask = False

  session_id = os.environ.get("GEMCODE_TERMINAL_SESSION_ID") or "terminal"
  user_id = os.environ.get("GEMCODE_TERMINAL_USER_ID") or "web-terminal"

  runner = create_runner(cfg, extra_tools=None)

  # Terminal.js expects the PTY output stream, so keep banners minimal.
  sys.stdout.write("")
  sys.stdout.flush()

  try:
    while True:
      line = sys.stdin.readline()
      if not line:
        break

      prompt = line.rstrip("\n").rstrip("\r").strip()
      if not prompt:
        continue

      if prompt.lower() in {"exit", "quit", "/exit", "/quit"}:
        break

      await run_mock_turn(prompt)
      if os.environ.get("GEMCODE_WEB_MOCK_RESPONSE"):
        continue

      await run_single_turn(
        runner=runner,
        cfg=cfg,
        prompt=prompt,
        user_id=user_id,
        session_id=session_id,
      )
  finally:
    try:
      await runner.close()
    except Exception:
      pass


def main() -> None:
  asyncio.run(repl())


if __name__ == "__main__":
  main()

