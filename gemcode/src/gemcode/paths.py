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


def resolve_under_allowed_roots(
  project_root: Path,
  rel: str,
  *,
  extra_roots: dict[str, Path] | None = None,
) -> tuple[Path, str]:
  """
  Resolve a path under the project root or an approved extra root.

  Supports:
  - Normal project-relative paths: "src/app.py"
  - Absolute paths: "/abs/path" (must be within project_root or an extra root)
  - Namespaced paths for extra roots: "<root_name>/path/to/file"
    where root_name is the basename alias we store for /add-dir.

  Returns (resolved_path, scope) where scope is "project" or "extra:<name>".
  """
  root = project_root.resolve()
  raw = Path(rel)

  # If the user provides an absolute path, allow it only if it's inside
  # project_root or one of the explicitly added roots.
  if raw.is_absolute():
    candidate = raw.resolve()
    try:
      candidate.relative_to(root)
      return candidate, "project"
    except ValueError:
      pass
    if extra_roots:
      for name, base in extra_roots.items():
        try:
          candidate.relative_to(base.resolve())
          return candidate, f"extra:{name}"
        except ValueError:
          continue
    raise PathEscapeError(f"Path outside allowed roots: {rel!r}")

  # If the path looks like "<extra_name>/...", resolve under that extra root.
  parts = raw.parts
  if extra_roots and parts:
    head = parts[0]
    if head in extra_roots:
      base = extra_roots[head].resolve()
      candidate = (base / Path(*parts[1:])).resolve()
      try:
        candidate.relative_to(base)
      except ValueError as e:
        raise PathEscapeError(f"Path outside allowed root '{head}': {rel!r}") from e
      return candidate, f"extra:{head}"

  # Default: project-relative.
  candidate = (root / raw).resolve()
  try:
    candidate.relative_to(root)
  except ValueError as e:
    raise PathEscapeError(f"Path outside project root: {rel!r}") from e
  return candidate, "project"
