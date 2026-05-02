"""Read, list, move files under project root."""

from __future__ import annotations

import shutil
from pathlib import Path

from gemcode.config import GemCodeConfig
from gemcode.paths import PathEscapeError, resolve_under_root, resolve_under_allowed_roots
from gemcode.trust import is_trusted_root


def make_filesystem_tools(cfg: GemCodeConfig):
  root = cfg.project_root
  trusted = is_trusted_root(root)
  extra_roots = getattr(cfg, "_added_dirs", None) or {}

  def _touch(rel_path: str) -> None:
    try:
      s = getattr(cfg, "_touched_paths", None)
      if s is None:
        s = set()
        setattr(cfg, "_touched_paths", s)
      s.add(str(rel_path).lstrip("./"))
    except Exception:
      pass

  def _checkpoint(op: str, paths: list[Path]) -> None:
    try:
      if not getattr(cfg, "enable_checkpoints", True):
        return
      from gemcode.checkpoints import create_checkpoint
      snaps = [(p, p.is_file()) for p in paths]
      create_checkpoint(project_root=root, op=op, file_snapshots=snaps)
    except Exception:
      return

  def read_file(
    path: str,
    max_bytes: int = 80_000,
    start_line: int = 1,
    end_line: int = 0,
  ) -> dict:
    """
    Read a text file relative to the project root.

    IMPORTANT: ALWAYS use read_file before editing a file. Never propose changes
    to code you haven't read — the mental model is always wrong without reading.
    Never use bash("cat file") or bash("head file") — use read_file instead.

    For large files, use start_line / end_line to read a specific range (1-indexed, inclusive):
      read_file("app.py", start_line=100, end_line=200)  — lines 100-200
      read_file("app.py", start_line=500)                — line 500 to end
    This is efficient — loads only the needed slice into context.

    When multiple files are needed, issue all read_file calls in the same turn
    (parallel reads) rather than sequentially.
    """
    if not trusted:
      return {"error": "Project folder is not trusted. Re-run GemCode and approve folder trust."}
    try:
      p, scope = resolve_under_allowed_roots(root, path, extra_roots=extra_roots)
    except PathEscapeError as e:
      return {"error": str(e)}
    if not p.is_file():
      return {"error": f"Not a file: {path}", "error_kind": "not_found"}
    _touch(path)

    # Dynamic caps: allow bigger reads when context is healthy, tighten when tight.
    try:
      from gemcode.dynamic_policy import get_dynamic_caps
      caps = get_dynamic_caps(cfg)
      if isinstance(max_bytes, int) and max_bytes > caps.read_file_max_bytes:
        max_bytes = caps.read_file_max_bytes
    except Exception:
      pass
    total_bytes = p.stat().st_size
    data = p.read_bytes()
    text_full = data.decode("utf-8", errors="replace")

    # Apply line range filter when requested
    if start_line != 1 or end_line > 0:
      lines = text_full.splitlines(keepends=True)
      total_lines = len(lines)
      s = max(1, start_line) - 1       # convert to 0-indexed
      e = end_line if end_line > 0 else total_lines
      e = min(e, total_lines)
      sliced = lines[s:e]
      text_full = "".join(sliced)
      # Encode back to bytes to apply max_bytes cap consistently
      data_sliced = text_full.encode("utf-8")
      truncated = len(data_sliced) > max_bytes
      text = text_full[:max_bytes]
      return {
        "path": path,
        "scope": scope,
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
      "scope": scope,
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
      return {"error": f"Source does not exist: {src}", "error_kind": "not_found"}
    if dest_p.exists():
      return {"error": f"Destination already exists: {dest}. Delete it first or choose a different name.", "error_kind": "conflict"}
    _touch(src)
    _touch(dest)
    # Checkpoint the source file state (so undo can restore it).
    _checkpoint("move_file", [src_p])
    dest_p.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src_p), str(dest_p))
    return {"src": src, "dest": dest, "moved": True}

  def list_directory(path: str = ".") -> dict:
    """
    List files and directories under a path (relative to project root).

    Use this instead of bash("ls ...") — it needs no permission and is instant.
    Prefer for directory exploration before any editing or execution.
    Issue in parallel with glob_files when you need both structure and file matches.
    """
    if not trusted:
      return {"error": "Project folder is not trusted. Re-run GemCode and approve folder trust."}
    try:
      p, scope = resolve_under_allowed_roots(root, path, extra_roots=extra_roots)
    except PathEscapeError as e:
      return {"error": str(e)}
    if not p.is_dir():
      return {"error": f"Not a directory: {path}"}
    _touch(path)
    entries: list[dict] = []
    for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
      entries.append(
        {
          "name": child.name,
          "type": "dir" if child.is_dir() else "file",
        }
      )
    return {"path": path, "scope": scope, "entries": entries[:500]}

  def glob_files(pattern: str) -> dict:
    """
    Find files by glob pattern relative to project root (e.g. 'src/**/*.py').

    Use this instead of bash("find . -name '*.py'") — it needs no permission.
    Supports recursive patterns: '**/*.ts', 'src/**/test_*.py', '**/config*.json'.
    Can be issued in parallel with list_directory and grep_content in the same turn.
    Returns up to 200 matches.
    """
    if not trusted:
      return {"error": "Project folder is not trusted. Re-run GemCode and approve folder trust."}
    if ".." in pattern or pattern.startswith("/"):
      return {"error": "Invalid pattern"}
    matches: list[str] = []
    base = root
    scope = "project"
    # Allow pattern like "<extra_name>/**" to search in that added root.
    if extra_roots:
      head = pattern.split("/", 1)[0]
      if head in extra_roots:
        base = extra_roots[head]
        scope = f"extra:{head}"
        pattern = pattern.split("/", 1)[1] if "/" in pattern else "*"
    for m in base.glob(pattern):
      try:
        rel_path = m.resolve()
        rel = rel_path.relative_to(root)
        rel_s = str(rel)
      except ValueError:
        if scope.startswith("extra:"):
          name = scope.split(":", 1)[1]
          try:
            rel2 = rel_path.relative_to(extra_roots[name].resolve())
            rel_s = f"{name}/{rel2}"
          except Exception:
            continue
        else:
          continue
      matches.append(rel_s)
      if len(matches) >= 200:
        break
    for rel in matches[:50]:
      _touch(rel)
    return {"pattern": pattern, "scope": scope, "matches": matches}

  def delete_file(path: str) -> dict:
    """Delete a file relative to the project root (not directories)."""
    if not trusted:
      return {"error": "Project folder is not trusted. Re-run GemCode and approve folder trust."}
    try:
      p = resolve_under_root(root, path)
    except PathEscapeError as e:
      return {"error": str(e)}
    if not p.is_file():
      return {"error": f"Not a file: {path}", "error_kind": "not_found"}
    _touch(path)
    _checkpoint("delete_file", [p])
    p.unlink()
    return {"path": path, "deleted": True}

  return read_file, list_directory, glob_files, delete_file, move_file
