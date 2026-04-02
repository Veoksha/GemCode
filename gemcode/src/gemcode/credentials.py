"""Persisted Google API credentials (Claude Code–style: save once, override via env)."""

from __future__ import annotations

import json
import os
from pathlib import Path


def user_config_dir() -> Path:
  return Path(os.environ.get("GEMCODE_HOME") or (Path.home() / ".gemcode"))


def credentials_path() -> Path:
  return user_config_dir() / "credentials.json"


def load_saved_google_api_key() -> str | None:
  p = credentials_path()
  try:
    data = json.loads(p.read_text(encoding="utf-8"))
  except (FileNotFoundError, json.JSONDecodeError, OSError):
    return None
  if not isinstance(data, dict):
    return None
  k = data.get("google_api_key")
  if isinstance(k, str) and k.strip():
    return k.strip()
  return None


def save_google_api_key_to_user_store(key: str) -> None:
  p = credentials_path()
  p.parent.mkdir(parents=True, exist_ok=True)
  payload = {"google_api_key": key.strip()}
  p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
  try:
    os.chmod(p, 0o600)
  except OSError:
    pass


def apply_saved_google_api_key_to_environ() -> None:
  """If ``GOOGLE_API_KEY`` is unset, load from the user credentials file."""
  if os.environ.get("GOOGLE_API_KEY"):
    return
  k = load_saved_google_api_key()
  if k:
    os.environ["GOOGLE_API_KEY"] = k
