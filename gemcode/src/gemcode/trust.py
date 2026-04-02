from __future__ import annotations

import json
import os
from pathlib import Path


def _trust_file_path() -> Path:
  base = Path(os.environ.get("GEMCODE_HOME") or (Path.home() / ".gemcode"))
  return base / "trust.json"


def load_trusted_roots() -> set[str]:
  """
  Returns a set of resolved absolute paths (as strings) that are trusted.
  """
  p = _trust_file_path()
  try:
    data = json.loads(p.read_text("utf-8"))
  except FileNotFoundError:
    return set()
  except Exception:
    return set()
  roots = data.get("trusted_roots") if isinstance(data, dict) else None
  if not isinstance(roots, list):
    return set()
  out: set[str] = set()
  for r in roots:
    if isinstance(r, str) and r:
      out.add(str(Path(r).resolve()))
  return out


def save_trusted_roots(roots: set[str]) -> None:
  p = _trust_file_path()
  p.parent.mkdir(parents=True, exist_ok=True)
  payload = {"trusted_roots": sorted(set(str(Path(r).resolve()) for r in roots))}
  p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def is_trusted_root(root: Path) -> bool:
  r = str(root.resolve())
  return r in load_trusted_roots()


def trust_root(root: Path, *, trusted: bool) -> None:
  roots = load_trusted_roots()
  r = str(root.resolve())
  if trusted:
    roots.add(r)
  else:
    roots.discard(r)
  save_trusted_roots(roots)

