"""Shared workspace path resolution for GemCode web HTTP handlers."""

from __future__ import annotations

import os
from pathlib import Path


def resolve_web_project_root(raw_path: str | None) -> Path:
  """
  Resolve the active project/workspace root for a web API request.

  Prefers an explicit ``path`` query/body param, then ``GEMCODE_WEB_PROJECT_ROOT``,
  then the API process working directory.
  """
  if raw_path and str(raw_path).strip():
    return Path(str(raw_path).strip()).expanduser().resolve()
  env = os.environ.get("GEMCODE_WEB_PROJECT_ROOT", "").strip()
  if env:
    return Path(env).expanduser().resolve()
  return Path.cwd().resolve()
