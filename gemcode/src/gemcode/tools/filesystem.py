"""Read, list, move files under project root."""

from __future__ import annotations

import shutil
from pathlib import Path

from gemcode.config import GemCodeConfig
from gemcode.paths import PathEscapeError, resolve_under_root
from gemcode.trust import is_trusted_root


def make_filesystem_tools(cfg: GemCodeConfig):
  root = cfg.project_root
  trusted = is_trusted_root(root)

  def read_file(
    path: str,
    max_bytes: int = 200_000,
    start_line: int = 1,
    end_line: int | None = None,
  ) -> dict:
    """
    Read a text file relative to the project root. Large files are truncated.

    Use start_line / end_line to read a specific line range (1-indexed, inclusive).
    This is efficient for large files — e.g. read_file("app.py", start_line=100, end_line=200)
    reads only lines 100–200 without loading the whole file into context.
    Omit end_line to read from start_line to end of file (still subject to max_bytes).
    """
    if not trusted:
      return {"error": "Project folder is not trusted. Re-run GemCode and approve folder trust."}
    try:
      p = resolve_under_root(root, path)
    except PathEscapeError as e:
      return {"error": str(e)}
    if not p.is_file():
      return {"error": f"Not a file: {path}"}
    total_bytes = p.stat().st_size
    data = p.read_bytes()
    text_full = data.decode("utf-8", errors="replace")

    # Apply line range filter when requested
    if start_line != 1 or end_line is not None:
      lines = text_full.splitlines(keepends=True)
      total_lines = len(lines)
      s = max(1, start_line) - 1       # convert to 0-indexed
      e = end_line if end_line is not None else total_lines
      e = min(e, total_lines)
      sliced = lines[s:e]
      text_full = "".join(sliced)
      # Encode back to bytes to apply max_bytes cap consistently
      data_sliced = text_full.encode("utf-8")
      truncated = len(data_sliced) > max_bytes
      text = text_full[:max_bytes]
      return {
        "path": path,
        "content": text,
        "start_line": s + 1,
        "end_line": min(e, s + len(sliced)),
        "total_lines": total_lines,
        "truncated": truncated,
        "total_bytes": total_bytes,
      }

    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    return {
      "path": path,
      "content": text,
      "truncated": truncated,
      "total_bytes": total_bytes,
    }

  def move_file(src: str, dest: str) -> dict:
    """
    Move or rename a file or directory within the project root.

    Both src and dest are paths relative to the project root.
    Creates parent directories for dest if needed.
    Use for: renaming files, reorganising directory structure.
    """
    if not trusted:
      return {"error": "Project folder is not trusted. Re-run GemCode and approve folder trust."}
    try:
      src_p = resolve_under_root(root, src)
      dest_p = resolve_under_root(root, dest)
    except PathEscapeError as e:
      return {"error": str(e)}
    if not src_p.exists():
      return {"error": f"Source does not exist: {src}"}
    if dest_p.exists():
      return {"error": f"Destination already exists: {dest}. Delete it first or choose a different name."}
    dest_p.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src_p), str(dest_p))
    return {"src": src, "dest": dest, "moved": True}

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

  return read_file, list_directory, glob_files, delete_file, move_file
