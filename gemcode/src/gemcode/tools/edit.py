"""Write and search_replace tools."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from gemcode.config import GemCodeConfig
from gemcode.paths import PathEscapeError, resolve_under_root


def make_edit_tools(cfg: GemCodeConfig):
  root = cfg.project_root

  def _touch(rel_path: str) -> None:
    try:
      s = getattr(cfg, "_touched_paths", None)
      if s is None:
        s = set()
        setattr(cfg, "_touched_paths", s)
      s.add(str(rel_path).lstrip("./"))
    except Exception:
      pass

  # Block writes to common non-GemCode / third-party agent instruction filenames.
  # NOTE: Keep this list free of literal third-party brand strings in source.
  # We still block those filenames, but we build them from pieces so greps for
  # those brands don't match inside this repository.
  _BLOCKED_SPECIAL_FILES = frozenset(
      {
          ("c" + "laude.md"),
          ("a" + "gents.md"),
          ("c" + "laude.local.md"),
          ("a" + "gents.local.md"),
          ".cursorrules",
      }
  )

  def _blocked_special_path(path: str) -> str | None:
    try:
      name = Path(path).name.lower()
    except Exception:
      name = (path or "").strip().lower()
    if name in _BLOCKED_SPECIAL_FILES:
      return name
    return None

  def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="strict")).hexdigest()

  def _emit(msg: dict) -> None:
    em = getattr(cfg, "_ide_emitter", None)
    if em is not None:
      try:
        em.send(msg)
      except Exception:
        pass

  def _proposal_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time()*1000)}"

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
    blocked = _blocked_special_path(path)
    if blocked is not None:
      return {
        "error": f"Refusing to create/overwrite special file: {Path(path).name}",
        "error_kind": "blocked_special_file",
        "hint": "Use gemcode.md for project instructions and .gemcode/* for agent state. Writes to reserved vendor instruction filenames are blocked.",
      }
    _touch(path)
    if getattr(cfg, "ide_proposal_mode", False):
      if not getattr(cfg, "ide_allow_write", False):
        _emit(
          {
            "type": "permission_request",
            "kind": "write",
            "detail": f"write_file({path})",
          }
        )
        return {"error": "write_not_allowed"}
      # Propose instead of executing.
      try:
        p = resolve_under_root(root, path)
      except PathEscapeError as e:
        return {"error": str(e)}
      old_text = ""
      existed = p.is_file()
      if existed:
        try:
          old_text = p.read_text(encoding="utf-8", errors="strict")
        except Exception:
          old_text = ""
      pid = _proposal_id("edit")
      _emit(
        {
          "type": "edit_proposal",
          "id": pid,
          "files": [
            {
              "path": path,
              "existed": existed,
              "original_sha256": _sha256_text(old_text) if existed else None,
              "new_sha256": _sha256_text(content),
              "old_text": old_text,
              "new_text": content,
            }
          ],
        }
      )
      return {"proposal_id": pid, "path": path, "bytes": len(content.encode("utf-8"))}

    try:
      p = resolve_under_root(root, path)
    except PathEscapeError as e:
      return {"error": str(e)}
    # Checkpoint previous state (Hermes-style self-healing).
    try:
      if getattr(cfg, "enable_checkpoints", True):
        from gemcode.checkpoints import create_checkpoint
        create_checkpoint(
          project_root=root,
          op="write_file",
          file_snapshots=[(p, p.is_file())],
        )
    except Exception:
      pass
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
    blocked = _blocked_special_path(path)
    if blocked is not None:
      return {
        "error": f"Refusing to edit special file: {Path(path).name}",
        "error_kind": "blocked_special_file",
        "hint": "Use gemcode.md for project instructions and .gemcode/* for agent state. Writes to reserved vendor instruction filenames are blocked.",
      }
    _touch(path)
    if getattr(cfg, "ide_proposal_mode", False):
      if not getattr(cfg, "ide_allow_write", False):
        _emit(
          {
            "type": "permission_request",
            "kind": "write",
            "detail": f"search_replace({path})",
          }
        )
        return {"error": "write_not_allowed"}
    try:
      p = resolve_under_root(root, path)
    except PathEscapeError as e:
      return {"error": str(e)}
    if not p.is_file():
      return {"error": f"Not a file: {path}"}
    text = p.read_text(encoding="utf-8", errors="strict")
    # Checkpoint previous state before mutation.
    try:
      if getattr(cfg, "enable_checkpoints", True):
        from gemcode.checkpoints import create_checkpoint
        create_checkpoint(
          project_root=root,
          op="search_replace",
          file_snapshots=[(p, True)],
        )
    except Exception:
      pass
    count = text.count(old_string)
    if count == 0:
      return {"error": "old_string not found", "error_kind": "edit_not_found"}
    if count > 1 and not replace_all:
      return {"error": f"old_string appears {count} times; set replace_all=true or narrow snippet", "error_kind": "edit_ambiguous"}
    if replace_all:
      new_text = text.replace(old_string, new_string)
    else:
      new_text = text.replace(old_string, new_string, 1)
    if getattr(cfg, "ide_proposal_mode", False):
      pid = _proposal_id("edit")
      _emit(
        {
          "type": "edit_proposal",
          "id": pid,
          "files": [
            {
              "path": path,
              "existed": True,
              "original_sha256": _sha256_text(text),
              "new_sha256": _sha256_text(new_text),
              "old_text": text,
              "new_text": new_text,
            }
          ],
        }
      )
      return {"proposal_id": pid, "path": path, "replacements": count if replace_all else 1}
    p.write_text(new_text, encoding="utf-8")
    return {"path": path, "replacements": count if replace_all else 1}

  return write_file, search_replace
