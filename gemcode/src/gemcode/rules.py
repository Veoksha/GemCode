from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rule:
  name: str
  path: Path
  text: str
  paths: list[str] | None = None


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
  m = _FRONTMATTER_RE.match(text or "")
  if not m:
    return {}, (text or "")
  raw = m.group(1)
  body = (text or "")[m.end() :]
  d: dict[str, str] = {}
  for line in raw.splitlines():
    if not line.strip() or line.strip().startswith("#"):
      continue
    if ":" not in line:
      continue
    k, v = line.split(":", 1)
    d[k.strip().lower()] = v.strip()
  return d, body


def _split_list(v: str | None) -> list[str] | None:
  if v is None:
    return None
  s = str(v).strip()
  if not s:
    return None
  if s.startswith("[") and s.endswith("]"):
    s = s[1:-1]
  parts = [p.strip().strip("'\"") for p in s.replace(",", " ").split()]
  out = [p for p in parts if p]
  return out or None


def _rule_dirs_for_project(project_root: Path) -> list[Path]:
  # Project has priority over personal.
  return [
    project_root / ".gemcode" / "rules",
    Path.home() / ".gemcode" / "rules",
  ]


def discover_rules(project_root: Path) -> list[Path]:
  """
  Returns all rule markdown files from project + personal.
  """
  out: list[Path] = []
  for d in _rule_dirs_for_project(project_root):
    if not d.is_dir():
      continue
    for p in sorted(d.rglob("*.md")):
      if p.is_file():
        out.append(p)
  return out


def _matches_any_path_gate(rule_paths: list[str] | None, touched_paths: list[str] | None) -> bool:
  if not rule_paths:
    return True
  if not touched_paths:
    return False
  import fnmatch

  for rp in rule_paths:
    for tp in touched_paths:
      if fnmatch.fnmatch(tp, rp):
        return True
  return False


def load_rules(project_root: Path, *, touched_paths: list[str] | None = None) -> list[Rule]:
  rules: list[Rule] = []
  for p in discover_rules(project_root):
    try:
      raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
      continue
    fm, body = _parse_frontmatter(raw)
    gates = _split_list(fm.get("paths"))
    if not _matches_any_path_gate(gates, touched_paths):
      continue
    name = (fm.get("name") or p.stem).strip()
    text = (body or "").strip()
    if not text:
      continue
    rules.append(Rule(name=name, path=p, text=text[:20_000], paths=gates))
  return rules


def build_rules_section(project_root: Path, *, touched_paths: list[str] | None = None) -> str:
  rules = load_rules(project_root, touched_paths=touched_paths)
  if not rules:
    return ""
  lines: list[str] = []
  lines.append("## Rules (project conventions)")
  lines.append("These are extra, repo-specific rules that must be followed when applicable.")
  for r in rules:
    lines.append(f"\n### {r.name}\n- source: {r.path}\n")
    lines.append(r.text)
  return "\n".join(lines).strip()

