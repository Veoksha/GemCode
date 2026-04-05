"""
Agent auto-notes tool — inspired by Claude Code's MEMORY.md auto-writing.

Claude Code lets Claude write its own notes to ~/.claude/projects/<proj>/MEMORY.md.
GemCode provides an equivalent: the agent can write structured notes to
.gemcode/notes.md which are loaded back at the start of each session
(via the GEMINI.md hierarchy in agent.py).

This is different from the persistent memory system (memories.jsonl):
  - memories.jsonl: semantic search-backed cross-session knowledge (requires /memory on)
  - notes.md: a free-form markdown notes file the agent curates manually
    — always loaded, zero-config, like a sticky notepad for project insights

The tool exposes two operations:
  - append_project_note(note): add a bullet point or section to notes.md
  - read_project_notes(): read current notes.md content

Usage by agent:
  When the agent discovers something worth remembering (build command, a tricky
  pattern, architecture insight, gotcha), it should call append_project_note to
  persist it for future sessions. The agent instruction tells it when to do this.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any


def _notes_path(project_root_str: str | None = None) -> Path:
  root = Path(project_root_str) if project_root_str else Path.cwd()
  return root / ".gemcode" / "notes.md"


def build_notes_tools(project_root: Path) -> list:
  """Return [append_project_note, read_project_notes] bound to this project."""

  notes_file = project_root / ".gemcode" / "notes.md"

  def append_project_note(note: str) -> dict[str, Any]:
    """
    Append a note to the project notes file (.gemcode/notes.md).

    Use this to record important insights, patterns, commands, or gotchas that
    will be useful in future sessions — like a persistent sticky notepad.

    Best for:
    - Build/test commands you discovered
    - Architecture patterns or important abstractions
    - Known issues or tricky edge cases
    - User preferences or workflow conventions
    - Important file locations or entry points

    Not for:
    - Every single thing the user says (be selective — only durable insights)
    - Sensitive data or credentials

    Args:
        note: The note to append. Use markdown. Start with a heading or bullet.
              Example: "- **Build**: `npm run build` (requires Node 20+)"
              Example: "## Architecture\\nAuth is handled by src/auth/middleware.ts"
    """
    if not note or not note.strip():
      return {"error": "note cannot be empty"}
    try:
      notes_file.parent.mkdir(parents=True, exist_ok=True)
      timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
      # If file doesn't exist, create with header
      if not notes_file.exists():
        header = f"# GemCode Agent Notes\n*Auto-generated project notes. Edit freely.*\n\n"
        notes_file.write_text(header, encoding="utf-8")
      current = notes_file.read_text(encoding="utf-8", errors="replace")
      # Avoid duplicating identical notes
      stripped = note.strip()
      if stripped in current:
        return {"status": "already_exists", "note": stripped}
      entry = f"\n<!-- {timestamp} -->\n{stripped}\n"
      notes_file.write_text(current + entry, encoding="utf-8")
      return {"status": "appended", "notes_path": str(notes_file)}
    except OSError as e:
      return {"error": f"Could not write notes: {e}"}

  def read_project_notes() -> dict[str, Any]:
    """
    Read the current contents of .gemcode/notes.md.

    Use this to check what has been previously noted about this project before
    starting a task — avoids re-discovering things already documented.

    Returns the full notes content as a string, or an empty string if no notes exist.
    """
    if not notes_file.exists():
      return {"content": "", "exists": False, "path": str(notes_file)}
    try:
      content = notes_file.read_text(encoding="utf-8", errors="replace")
      return {"content": content, "exists": True, "path": str(notes_file), "chars": len(content)}
    except OSError as e:
      return {"error": f"Could not read notes: {e}"}

  # Bind the project root to the function names so they show correctly in tool manifests
  append_project_note.__name__ = "append_project_note"
  read_project_notes.__name__ = "read_project_notes"

  return [append_project_note, read_project_notes]
