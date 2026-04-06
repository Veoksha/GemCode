"""Write and search_replace tools."""

from __future__ import annotations

from pathlib import Path

from gemcode.config import GemCodeConfig
from gemcode.paths import PathEscapeError, resolve_under_root


def make_edit_tools(cfg: GemCodeConfig):
  root = cfg.project_root

  def write_file(path: str, content: str) -> dict:
    """
    Create or overwrite a file with the given content.

    Path is relative to the project root.

    IMPORTANT: If the file already exists, READ it first with read_file() before
    calling write_file(). Overwriting without reading risks losing existing content.
    Only use write_file for NEW files or when you intend a complete replacement.
    For targeted in-place edits, use search_replace() instead.

    Never write non-textual content (binary, base64 blobs) — those belong in
    artifacts, not in source files.
    """
    try:
      p = resolve_under_root(root, path)
    except PathEscapeError as e:
      return {"error": str(e)}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"path": path, "bytes_written": len(content.encode("utf-8"))}

  def search_replace(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
  ) -> dict:
    """
    Perform an exact string replacement in a file.

    IMPORTANT: You MUST call read_file() on the file at least once before calling
    search_replace(). Editing a file you haven't read leads to wrong context
    and broken changes.

    Usage rules:
    - old_string must match the file EXACTLY (whitespace, indentation, line endings).
      The edit FAILS if old_string is not found, or if it appears more than once
      and replace_all is False.
    - Use the smallest old_string that is clearly unique — typically 3-5 lines of
      surrounding context is enough. Do not include 20+ lines of context when 4
      lines would uniquely identify the target location.
    - Set replace_all=True to rename a variable or rename a string across the whole file.
    - Always prefer search_replace over write_file for targeted edits — it preserves
      the rest of the file and makes the change reviewable.
    - Do NOT add emojis or comments that just explain what the code does. Only add
      comments that explain non-obvious intent or trade-offs.
    - NEVER propose edits before reading. Read first. Edit second.
    """
    try:
      p = resolve_under_root(root, path)
    except PathEscapeError as e:
      return {"error": str(e)}
    if not p.is_file():
      return {"error": f"Not a file: {path}"}
    text = p.read_text(encoding="utf-8", errors="strict")
    count = text.count(old_string)
    if count == 0:
      return {"error": "old_string not found"}
    if count > 1 and not replace_all:
      return {"error": f"old_string appears {count} times; set replace_all=true or narrow snippet"}
    if replace_all:
      new_text = text.replace(old_string, new_string)
    else:
      new_text = text.replace(old_string, new_string, 1)
    p.write_text(new_text, encoding="utf-8")
    return {"path": path, "replacements": count if replace_all else 1}

  return write_file, search_replace
