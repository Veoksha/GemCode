from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


_STORE_VERSION = 1
_MAX_BYTES = 2_500_000  # keep below common reverse-proxy limits


def _store_path(project_root: str) -> Path:
  return Path(project_root) / ".gemcode" / "ui_chat_store.json"


def _read_store(project_root: str) -> dict[str, Any]:
  path = _store_path(project_root)
  try:
    raw = path.read_text("utf-8")
  except FileNotFoundError:
    return {"ok": True, "state": None, "version": _STORE_VERSION, "updated_at_ms": 0}
  except OSError as exc:
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

  try:
    data = json.loads(raw)
  except json.JSONDecodeError:
    # Corrupt file — don't brick the UI; treat as empty.
    return {"ok": True, "state": None, "version": _STORE_VERSION, "updated_at_ms": 0}

  if not isinstance(data, dict):
    return {"ok": True, "state": None, "version": _STORE_VERSION, "updated_at_ms": 0}

  return {
    "ok": True,
    "state": data.get("state"),
    "version": _STORE_VERSION,
    "updated_at_ms": int(data.get("updated_at_ms") or 0),
  }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_suffix(".tmp")
  tmp.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
  tmp.replace(path)


def handle_ui_chat_store_get(project_root: str) -> tuple[int, dict[str, Any]]:
  data = _read_store(project_root)
  return (200 if data.get("ok") else 500), data


def handle_ui_chat_store_post(project_root: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
  # Accept either {state: <object>} or {state: <object>, updated_at_ms: <int>}
  state = body.get("state")
  if state is not None and not isinstance(state, (dict, list)):
    return 400, {"ok": False, "error": "state must be an object or array"}

  payload = {
    "state": state,
    "updated_at_ms": int(body.get("updated_at_ms") or time.time() * 1000),
    "store_version": _STORE_VERSION,
  }
  raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
  if len(raw) > _MAX_BYTES:
    return 413, {"ok": False, "error": "chat store too large"}

  try:
    _atomic_write_json(_store_path(project_root), payload)
  except OSError as exc:
    return 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

  return 200, {"ok": True}

