"""UX hints when the project root is unusually broad (e.g. user home)."""

from __future__ import annotations

from pathlib import Path


def project_root_is_user_home(project_root: Path) -> bool:
  try:
    return project_root.resolve() == Path.home().resolve()
  except OSError:
    return False


def narrow_workspace_tip(project_root: Path) -> str | None:
  """
  One-line suggestion when GemCode is anchored at ~ so searches span the whole account.
  """
  if not project_root_is_user_home(project_root):
    return None
  return (
    "Tip: narrow the workspace — restart with: gemcode -C /path/to/your/repo"
  )
