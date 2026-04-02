"""Write and search_replace tools."""

from __future__ import annotations

from pathlib import Path

from gemcode.config import GemCodeConfig
from gemcode.paths import PathEscapeError, resolve_under_root


def make_edit_tools(cfg: GemCodeConfig):
  root = cfg.project_root

  def write_file(path: str, content: str) -> dict:
    """Create or overwrite a file relative to the project root."""
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
    Replace old_string with new_string in a text file. Fails if old_string
    is missing or duplicate (unless replace_all=True).
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
