"""Safe path resolution under a project root."""

from __future__ import annotations

from pathlib import Path


class PathEscapeError(ValueError):
  """Resolved path would leave the project sandbox."""


def resolve_under_root(project_root: Path, rel: str) -> Path:
  """
  Resolve a user-relative path against project_root.

  Rejects absolute paths that escape the root (symlink traversal is still a
  concern for production; MVP uses realpath check).
  """
  root = project_root.resolve()
  raw = Path(rel)
  if raw.is_absolute():
    candidate = raw.resolve()
  else:
    candidate = (root / raw).resolve()
  try:
    candidate.relative_to(root)
  except ValueError as e:
    raise PathEscapeError(f"Path outside project root: {rel!r}") from e
  return candidate
