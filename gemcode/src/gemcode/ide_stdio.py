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
import base64
import binascii
import mimetypes
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig, load_cli_environment
from gemcode.ide_protocol import IdeEmitter, make_event, make_response, parse_json_line
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


def _max_ide_inline_bytes() -> int:
  raw = os.environ.get("GEMCODE_MAX_ATTACHMENT_BYTES")
  if raw:
    try:
      v = int(raw, 10)
      if v > 0:
        return v
    except ValueError:
      pass
  return 20 * 1024 * 1024


def _suffix_for_inline_attachment(name: str, mime: str) -> str:
  p = Path(name or "")
  if p.suffix and len(p.suffix) <= 12:
    return p.suffix
  m = (mime or "").strip().lower().split(";")[0].strip()
  if m:
    ext = mimetypes.guess_extension(m, strict=False)
    if ext == ".jpe":
      ext = ".jpeg"
    if ext:
      return ext
  return ".bin"


def prepare_inline_attachment_paths(
  attachments: list[Any] | None,
  *,
  max_bytes: int | None = None,
  max_count: int = 16,
) -> tuple[list[Path], list[str]]:
  """
  Materialize IDE ``inline`` / ``binary`` / ``blob`` attachment dicts as temp files.

  Expected keys (camelCase accepted): ``data`` or ``base64``, optional ``name`` /
  ``filename``, ``mime_type`` / ``mimeType``.

  Returns ``(paths, errors)``. Caller must unlink paths when done.
  """
  cap = max_bytes if max_bytes is not None else _max_ide_inline_bytes()
  max_b64 = int(cap * 4 / 3) + 32
  paths: list[Path] = []
  errors: list[str] = []
  if not attachments:
    return paths, errors

  for a in attachments:
    if len(paths) >= max_count:
      errors.append(f"inline attachments: max {max_count} files")
      break
    if not isinstance(a, dict):
      continue
    at = str(a.get("type") or "").strip().lower()
    if at not in ("inline", "binary", "blob"):
      continue
    b64 = a.get("data") if isinstance(a.get("data"), str) else None
    if b64 is None:
      b64 = a.get("base64") if isinstance(a.get("base64"), str) else None
    if not b64:
      errors.append("inline attachment missing base64 data")
      continue
    if len(b64) > max_b64:
      errors.append("inline attachment too large (base64)")
      continue
    try:
      raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as e:
      errors.append(f"inline attachment: invalid base64 ({e})")
      continue
    if len(raw) > cap:
      errors.append(f"inline attachment exceeds max {cap} bytes")
      continue
    name = str(a.get("name") or a.get("filename") or "attachment")
    mime = str(a.get("mime_type") or a.get("mimeType") or "")
    suffix = _suffix_for_inline_attachment(name, mime)
    try:
      fd, fspath = tempfile.mkstemp(prefix="gemcode_ide_", suffix=suffix)
      with os.fdopen(fd, "wb") as f:
        f.write(raw)
      paths.append(Path(fspath))
    except OSError as e:
      errors.append(f"inline attachment temp file failed: {e}")

  return paths, errors


def _textual_attachments_only(attachments: list[dict] | None) -> list[dict]:
  if not attachments:
    return []
  out: list[dict] = []
  for a in attachments:
    if not isinstance(a, dict):
      continue
    at = str(a.get("type") or "").strip().lower()
    if at in ("inline", "binary", "blob"):
      continue
    out.append(a)
  return out


def _build_prompt(prompt: str, attachments: list[dict] | None) -> str:
  # Keep it simple: attachments are appended as fenced blocks.
  if not attachments:
    return prompt
  parts = [prompt.rstrip()]
  for a in attachments:
    if not isinstance(a, dict):
      continue
    at = (a.get("type") or "").strip().lower()
    if at in ("inline", "binary", "blob"):
      continue
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
  # Avoid google-genai printing a precedence warning that would corrupt stdout.
  try:
    if os.environ.get("GOOGLE_API_KEY") and os.environ.get("GEMINI_API_KEY"):
      os.environ.pop("GEMINI_API_KEY", None)
  except Exception:
    pass
  # Also silence noisy library warnings that would corrupt stdout parsing.
  try:
    import logging
    _msg = "Both GOOGLE_API_KEY and GEMINI_API_KEY are set. Using GOOGLE_API_KEY."

    class _DropNoisyApiKeyWarning(logging.Filter):
      def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        try:
          return _msg not in str(record.getMessage() or "")
        except Exception:
          return True

    root_logger = logging.getLogger()
    root_logger.addFilter(_DropNoisyApiKeyWarning())
    for name in ("google", "google.genai", "google.genai._api_client"):
      try:
        logging.getLogger(name).addFilter(_DropNoisyApiKeyWarning())
        logging.getLogger(name).setLevel(logging.ERROR)
      except Exception:
        pass
  except Exception:
    pass
  emitter = IdeEmitter(stream=proto_out)
  emitter.send(make_event(event="hello", protocol=2))

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
        emitter.send(make_event(event="bye"))
        return 0

      if mtype != "request":
        emitter.send(make_event(event="error", error=f"unknown_type:{mtype}"))
        continue

      req_id = str(msg.get("id") or "")
      action = str(msg.get("action") or "")
      if not req_id:
        emitter.send(make_event(event="error", error="missing_id"))
        continue

      if action == "cancel":
        # Best-effort: we don't yet interrupt ADK mid-flight; we just ack.
        emitter.send(make_response(id=req_id, ok=True, cancelled=True))
        continue

      if action != "turn":
        emitter.send(make_response(id=req_id, ok=False, error=f"unknown_action:{action}"))
        continue

      # Lazily initialize runner on first turn (needs project root).
      if cfg is None:
        root = msg.get("project_root") or os.getcwd()
        model = msg.get("model") or os.environ.get("GEMCODE_MODEL") or ""
        cfg = GemCodeConfig(project_root=Path(str(root)), model=str(model))
        # Attach emitter + proposal mode flags (used by tool wrappers).
        object.__setattr__(cfg, "_ide_emitter", emitter)
        object.__setattr__(cfg, "ide_proposal_mode", True)
        runner = create_runner(cfg, extra_tools=None)

      if session_id is None:
        session_id = str(msg.get("session") or "vscode")

      prompt = str(msg.get("prompt") or "")
      attachments = msg.get("attachments") if isinstance(msg.get("attachments"), list) else None
      att_dicts = [a for a in (attachments or []) if isinstance(a, dict)]
      inline_paths, inline_err = prepare_inline_attachment_paths(attachments)
      if inline_err:
        for p in inline_paths:
          try:
            p.unlink()
          except OSError:
            pass
        emitter.send(
            make_response(
                id=req_id,
                ok=False,
                error="; ".join(inline_err),
                session=session_id,
            )
        )
        continue

      full_prompt = _build_prompt(prompt, _textual_attachments_only(att_dicts))

      # Per-turn allow flags (the engine still only proposes in IDE mode; the IDE applies).
      allow_write = _truthy(msg.get("allowWrite"), default=False)
      allow_shell = _truthy(msg.get("allowShell"), default=False)
      object.__setattr__(cfg, "ide_allow_write", bool(allow_write))
      object.__setattr__(cfg, "ide_allow_shell", bool(allow_shell))

      emitter.send(make_event(event="turn_start", id=req_id, session=session_id))
      try:
        try:
          events = await run_turn(
              runner,
              user_id="local",
              session_id=session_id,
              prompt=full_prompt,
              max_llm_calls=cfg.max_llm_calls,
              cfg=cfg,
              attachment_paths=inline_paths if inline_paths else None,
          )
        finally:
          for p in inline_paths:
            try:
              p.unlink()
            except OSError:
              pass
      except Exception as e:
        emitter.send(make_response(id=req_id, ok=False, error=f"{type(e).__name__}: {e}", session=session_id))
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
        emitter.send(make_event(event="text", id=req_id, text=out_text))
      emitter.send(make_response(id=req_id, ok=True, session=session_id))

  finally:
    if runner is not None:
      try:
        await runner.close()
      except Exception:
        pass
  return 0


def main() -> None:
  raise SystemExit(asyncio.run(run_stdio_loop()))

