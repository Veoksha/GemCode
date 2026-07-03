"""Shared workspace path resolution for GemCode web HTTP handlers."""

from __future__ import annotations

import os
from pathlib import Path


class HostedTenantPathError(ValueError):
  """Client requested a path outside the locked tenant workspace."""


def hosted_tenant_root() -> Path | None:
  """When set, web handlers are locked to this directory (multi-tenant hosting)."""
  raw = os.environ.get("GEMCODE_HOSTED_TENANT_ROOT", "").strip()
  if not raw:
    return None
  return Path(raw).expanduser().resolve()


def _is_within_root(root: Path, candidate: Path) -> bool:
  try:
    candidate.resolve().relative_to(root.resolve())
    return True
  except ValueError:
    return False


def resolve_web_project_root(raw_path: str | None) -> Path:
  """
  Resolve the active project/workspace root for a web API request.

  Prefers an explicit ``path`` query/body param, then ``GEMCODE_WEB_PROJECT_ROOT``,
  then the API process working directory.

  When ``GEMCODE_HOSTED_TENANT_ROOT`` is set (hosted multi-tenant mode), client
  paths must stay inside that root; otherwise the locked root is always used.
  """
  locked = hosted_tenant_root()
  if locked is not None:
    if raw_path and str(raw_path).strip():
      candidate = Path(str(raw_path).strip()).expanduser().resolve()
      if not _is_within_root(locked, candidate):
        raise HostedTenantPathError(
          f"path is outside tenant workspace: {candidate} (root={locked})"
        )
      return candidate
    return locked

  if raw_path and str(raw_path).strip():
    return Path(str(raw_path).strip()).expanduser().resolve()
  env = os.environ.get("GEMCODE_WEB_PROJECT_ROOT", "").strip()
  if env:
    return Path(env).expanduser().resolve()
  return Path.cwd().resolve()


def resolve_sse_project_root(raw_path: str | None) -> Path:
  """Same rules as :func:`resolve_web_project_root` for SSE chat subprocess."""
  return resolve_web_project_root(raw_path)
