"""
`gemcode ide --stdio`

Long-lived engine process that communicates over stdin/stdout using JSONL.

The IDE is responsible for:
- presenting UI
- previewing diffs
- applying changes (WorkspaceEdit)

GemCode is responsible for:
- planning + tool calls
- proposing edits/commands (when in proposal mode)
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from gemcode.config import GemCodeConfig, load_cli_environment
from gemcode.ide_protocol import IdeEmitter, parse_json_line
from gemcode.invoke import run_turn
from gemcode.session_runtime import create_runner


def _truthy(v: Any, default: bool = False) -> bool:
  if v is None:
    return default
  if isinstance(v, bool):
    return v
  if isinstance(v, (int, float)):
    return bool(v)
  if isinstance(v, str):
    return v.strip().lower() in ("1", "true", "yes", "on")
  return default


def _build_prompt(prompt: str, attachments: list[dict] | None) -> str:
  # Keep it simple: attachments are appended as fenced blocks.
  if not attachments:
    return prompt
  parts = [prompt.rstrip()]
  for a in attachments:
    if not isinstance(a, dict):
      continue
    at = (a.get("type") or "").strip().lower()
    if at == "selection":
      txt = a.get("text") or ""
      path = a.get("path") or ""
      rng = a.get("range") or ""
      header = f"Selection from {path}{(' ' + rng) if rng else ''}".strip()
      parts.append(f"\n\n```text\n{header}\n{txt}\n```")
    elif at == "file":
      path = a.get("path") or ""
      snippet = a.get("text") or ""
      header = f"File context: {path}".strip()
      parts.append(f"\n\n```text\n{header}\n{snippet}\n```")
  return "\n".join(parts).strip() + "\n"


async def run_stdio_loop() -> int:
  load_cli_environment()
  # Keep stdout reserved for protocol JSONL. Redirect accidental prints to stderr.
  proto_out = sys.stdout
  try:
    sys.stdout = sys.stderr  # type: ignore[assignment]
  except Exception:
    pass
  emitter = IdeEmitter(stream=proto_out)
  emitter.send({"type": "hello", "protocol": 1})

  runner = None
  cfg: GemCodeConfig | None = None
  session_id: str | None = None

  try:
    for raw in sys.stdin:
      msg = parse_json_line(raw)
      mtype = msg.get("type")
      if mtype in ("invalid", None):
        emitter.send({"type": "error", "error": msg.get("error") or "invalid"})
        continue

      if mtype == "shutdown":
        emitter.send({"type": "bye"})
        return 0

      if mtype != "turn":
        emitter.send({"type": "error", "error": f"unknown_type:{mtype}"})
        continue

      # Lazily initialize runner on first turn (needs project root).
      if cfg is None:
        root = msg.get("project_root") or os.getcwd()
        model = msg.get("model") or os.environ.get("GEMCODE_MODEL") or ""
        from pathlib import Path
        cfg = GemCodeConfig(project_root=Path(str(root)), model=str(model))
        # Attach emitter + proposal mode flags (used by tool wrappers).
        object.__setattr__(cfg, "_ide_emitter", emitter)
        object.__setattr__(cfg, "ide_proposal_mode", True)
        runner = create_runner(cfg, extra_tools=None)

      if session_id is None:
        session_id = str(msg.get("session") or "vscode")

      prompt = str(msg.get("prompt") or "")
      attachments = msg.get("attachments") if isinstance(msg.get("attachments"), list) else None
      full_prompt = _build_prompt(prompt, attachments)

      # Per-turn allow flags (the engine still only proposes in IDE mode; the IDE applies).
      allow_write = _truthy(msg.get("allowWrite"), default=False)
      allow_shell = _truthy(msg.get("allowShell"), default=False)
      object.__setattr__(cfg, "ide_allow_write", bool(allow_write))
      object.__setattr__(cfg, "ide_allow_shell", bool(allow_shell))

      emitter.send({"type": "turn_start", "session": session_id})
      try:
        events = await run_turn(
          runner,
          user_id="local",
          session_id=session_id,
          prompt=full_prompt,
          max_llm_calls=cfg.max_llm_calls,
          cfg=cfg,
        )
      except Exception as e:
        emitter.send({"type": "error", "error": f"{type(e).__name__}: {e}"})
        emitter.send({"type": "turn_done", "session": session_id, "ok": False})
        continue

      # Emit assistant text as a single message for now (delta streaming can be added later).
      txt_parts: list[str] = []
      for ev in events:
        try:
          if not getattr(ev, "content", None) or not ev.content.parts:
            continue
          if getattr(ev, "author", None) == "user":
            continue
          for p in ev.content.parts:
            t = getattr(p, "text", None)
            if t:
              txt_parts.append(t)
        except Exception:
          continue
      out_text = "".join(txt_parts).strip()
      if out_text:
        emitter.send({"type": "text", "text": out_text})
      emitter.send({"type": "turn_done", "session": session_id, "ok": True})

  finally:
    if runner is not None:
      try:
        await runner.close()
      except Exception:
        pass
  return 0


def main() -> None:
  raise SystemExit(asyncio.run(run_stdio_loop()))

