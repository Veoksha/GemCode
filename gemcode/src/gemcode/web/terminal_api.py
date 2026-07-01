"""HTTP handler for web terminal line turns (wraps terminal_repl.run_single_turn)."""

from __future__ import annotations

import asyncio
import io
import os
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig
from gemcode.session_runtime import create_runner
from gemcode.web.terminal_repl import run_mock_turn, run_single_turn


from gemcode.web.project_root import resolve_web_project_root


def _resolve_root(raw_path: str) -> Path:
  return resolve_web_project_root(raw_path)


async def _terminal_turn_async(
  project_root: Path,
  *,
  prompt: str,
  session_id: str,
  user_id: str,
) -> dict[str, Any]:
  from gemcode.cli import _initialize_gemcode_project

  cfg = GemCodeConfig(project_root=project_root)
  cfg.permission_mode = os.environ.get("GEMCODE_PERMISSION_MODE", cfg.permission_mode)
  cfg.yes_to_all = os.environ.get("GEMCODE_WEB_YES_TO_ALL", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
  )
  cfg.interactive_permission_ask = False
  _initialize_gemcode_project(cfg)

  buf = io.StringIO()
  runner = create_runner(cfg, extra_tools=None)
  try:
    with redirect_stdout(buf):
      await run_mock_turn(prompt)
      if not os.environ.get("GEMCODE_WEB_MOCK_RESPONSE"):
        await run_single_turn(
          runner=runner,
          cfg=cfg,
          prompt=prompt,
          user_id=user_id,
          session_id=session_id,
        )
  finally:
    try:
      close = runner.close()
      if asyncio.iscoroutine(close):
        await close
    except Exception:
      pass

  return {"ok": True, "output": buf.getvalue(), "session_id": session_id}


def handle_terminal_post(data: dict[str, Any], raw_path: str) -> tuple[int, dict[str, Any]]:
  root = _resolve_root(raw_path)
  if not root.is_dir():
    return 400, {"ok": False, "error": "project path is not a directory", "path": str(root)}

  prompt = str(data.get("prompt") or data.get("line") or "").strip()
  if not prompt:
    return 400, {"ok": False, "error": "prompt is required"}

  session_id = str(data.get("session_id") or "web-terminal")
  user_id = str(data.get("user_id") or "web-terminal")

  if not os.environ.get("GEMCODE_WEB_MOCK_RESPONSE") and not (
    os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
  ):
    return 503, {
      "ok": False,
      "error": "GOOGLE_API_KEY is not set — add it in Settings or your .env file",
    }

  try:
    payload = asyncio.run(
      _terminal_turn_async(root, prompt=prompt, session_id=session_id, user_id=user_id)
    )
    return 200, payload
  except Exception as exc:
    return 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
