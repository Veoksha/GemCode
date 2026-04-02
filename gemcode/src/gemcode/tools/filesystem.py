"""Read and list files under project root."""

from __future__ import annotations

from pathlib import Path

from gemcode.config import GemCodeConfig
from gemcode.paths import PathEscapeError, resolve_under_root
from gemcode.trust import is_trusted_root


def make_filesystem_tools(cfg: GemCodeConfig):
  root = cfg.project_root
  trusted = is_trusted_root(root)

  def read_file(path: str, max_bytes: int = 200_000) -> dict:
    """Read a text file relative to the project root. Large files are truncated."""
    if not trusted:
      return {"error": "Project folder is not trusted. Re-run GemCode and approve folder trust."}
    try:
      p = resolve_under_root(root, path)
    except PathEscapeError as e:
      return {"error": str(e)}
    if not p.is_file():
      return {"error": f"Not a file: {path}"}
    data = p.read_bytes()
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    return {
      "path": path,
      "content": text,
      "truncated": truncated,
      "total_bytes": len(data),
    }

  def list_directory(path: str = ".") -> dict:
    """List files and directories under path (relative to project root)."""
    if not trusted:
      return {"error": "Project folder is not trusted. Re-run GemCode and approve folder trust."}
    try:
      p = resolve_under_root(root, path)
    except PathEscapeError as e:
      return {"error": str(e)}
    if not p.is_dir():
      return {"error": f"Not a directory: {path}"}
    entries: list[dict] = []
    for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
      entries.append(
        {
          "name": child.name,
          "type": "dir" if child.is_dir() else "file",
        }
      )
    return {"path": path, "entries": entries[:500]}

  def glob_files(pattern: str) -> dict:
    """Glob file paths relative to project root (e.g. 'src/**/*.py')."""
    if not trusted:
      return {"error": "Project folder is not trusted. Re-run GemCode and approve folder trust."}
    if ".." in pattern or pattern.startswith("/"):
      return {"error": "Invalid pattern"}
    matches: list[str] = []
    for m in root.glob(pattern):
      try:
        rel = m.resolve().relative_to(root)
      except ValueError:
        continue
      matches.append(str(rel))
      if len(matches) >= 200:
        break
    return {"pattern": pattern, "matches": matches}

  def delete_file(path: str) -> dict:
    """Delete a file relative to the project root (not directories)."""
    if not trusted:
      return {"error": "Project folder is not trusted. Re-run GemCode and approve folder trust."}
    try:
      p = resolve_under_root(root, path)
    except PathEscapeError as e:
      return {"error": str(e)}
    if not p.is_file():
      return {"error": f"Not a file: {path}"}
    p.unlink()
    return {"path": path, "deleted": True}

  return read_file, list_directory, glob_files, delete_file
