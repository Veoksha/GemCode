from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OutputStyle:
  name: str
  path: Path
  text: str


def _is_valid_name(name: str) -> bool:
  return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name or ""))


def _builtin_style_dir() -> Path:
  # Built-in styles shipped with GemCode (lowest priority).
  # Located under the python package so they work out-of-the-box.
  return Path(__file__).resolve().parent / "builtin" / "output-styles"


def _style_dirs_for_project(project_root: Path) -> list[Path]:
  # Priority (highest last): project > personal > built-in.
  return [
    project_root / ".gemcode" / "output-styles",
    Path.home() / ".gemcode" / "output-styles",
    _builtin_style_dir(),
  ]


def discover_output_styles(project_root: Path) -> dict[str, Path]:
  """
  Return name -> path. Project overrides personal when same name exists.
  """
  found: dict[str, Path] = {}
  # low->high overwrite so higher priority wins
  dirs = list(reversed(_style_dirs_for_project(project_root)))
  for d in dirs:
    if not d.is_dir():
      continue
    for p in d.iterdir():
      if not p.is_file():
        continue
      if p.suffix.lower() != ".md":
        continue
      name = p.stem.strip().lower()
      if not _is_valid_name(name):
        continue
      found[name] = p
  return found


def load_output_style(project_root: Path, name: str) -> OutputStyle | None:
  styles = discover_output_styles(project_root)
  k = (name or "").strip().lower()
  if not k or k not in styles:
    return None
  p = styles[k]
  try:
    text = p.read_text(encoding="utf-8", errors="replace").strip()
  except OSError:
    return None
  if not text:
    return None
  # Cap so styles don't explode prompt size.
  text = text[:20_000]
  return OutputStyle(name=k, path=p, text=text)


def build_output_style_section(project_root: Path, name: str | None) -> str:
  if not name:
    return ""
  s = load_output_style(project_root, name)
  if s is None:
    return ""
  return (
    "## Output style (active)\n"
    f"- **name**: {s.name}\n"
    f"- **source**: {s.path}\n\n"
    f"{s.text}\n"
  ).strip()

