from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GemSkillMeta:
  name: str
  description: str
  disable_model_invocation: bool = False
  user_invocable: bool = True
  allowed_tools: list[str] | None = None
  paths: list[str] | None = None
  context: str | None = None  # e.g. "fork"
  agent: str | None = None
  model: str | None = None


@dataclass(frozen=True)
class GemSkill:
  meta: GemSkillMeta
  skill_dir: Path
  skill_md: Path
  body_markdown: str


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)

_BUILTIN_SKILLS: dict[str, tuple[GemSkillMeta, str]] = {
  "batch": (
    GemSkillMeta(
      name="batch",
      description=(
        "Orchestrate large-scale codebase changes in parallel. Maps the repo, "
        "decomposes into independent units, runs subagents per unit, verifies, "
        "and synthesizes a final report."
      ),
      disable_model_invocation=True,  # manual only; it's heavy.
      user_invocable=True,
    ),
    (
      "## Batch workflow (parallel)\n"
      "Use this skill for large changes that benefit from decomposition.\n\n"
      "### Phase 0: Safety + scope\n"
      "- Confirm the target scope from `$ARGUMENTS`.\n"
      "- If not in a git repo, still proceed but rely on checkpoints and `/diff`.\n"
      "- Keep changes incremental; avoid sweeping rewrites unless required.\n\n"
      "### Phase 1: Map and decompose\n"
      "1. Run `repo_map('.')` to get a compact overview.\n"
      "2. Identify 5–30 independent work units (files/modules/features).\n"
      "3. Create a todo list with one item per unit.\n"
      "4. For each unit: specify target files, success criteria, and minimal test.\n\n"
      "### Phase 2: Execute in parallel\n"
      "- Spawn one subagent per unit using `run_subtask`.\n"
      "- Each subagent must:\n"
      "  - read relevant files\n"
      "  - make minimal edits\n"
      "  - run the smallest meaningful verification (tests/lints) if possible\n"
      "  - report what changed and what remains\n\n"
      "### Phase 3: Verify + integrate\n"
      "- Reconcile overlapping edits; resolve conflicts deterministically.\n"
      "- Run a final verification pass (tests/lints/smoke) when applicable.\n"
      "- Produce a final summary: what changed, where, why, and how to validate.\n\n"
      "### Output format\n"
      "- **Plan**: bullets with units\n"
      "- **Execution**: per-unit results\n"
      "- **Final**: verification + next steps\n"
    ),
  ),
  "compress-memory": (
    GemSkillMeta(
      name="compress-memory",
      description=(
        "Compress a markdown memory file (gemcode.md, .gemcode notes, todos) into a terse style "
        "to reduce input tokens, while preserving code blocks, headings, and URLs."
      ),
      disable_model_invocation=False,
      user_invocable=True,
    ),
    (
      "## Compress memory file\n"
      "Use this skill to rewrite a markdown-like memory file into a more token-efficient form.\n\n"
      "### When to use\n"
      "- The user asks to compress gemcode.md, .gemcode notes, preferences, or other prose-heavy markdown.\n"
      "- The user wants fewer input tokens each session.\n\n"
      "### Safety and boundaries\n"
      "- ONLY run on markdown-like files (.md/.txt/.rst, or extensionless files under .gemcode/).\n"
      "- NEVER run on secret/credential/key files (.env, credentials, .ssh, .aws, *.pem, etc.).\n"
      "- This operation sends file content to the Gemini API.\n"
      "- Tool will create a backup: `<stem>.original.md` and abort if backup already exists.\n"
      "- Tool validates: headings, fenced code blocks, URLs. On failure, it restores the original.\n\n"
      "### How to run\n"
      "1. Confirm target file path from `$ARGUMENTS`.\n"
      "2. Pick mode: `lite`, `full`, or `ultra` (default `full`).\n"
      "3. Call the tool:\n\n"
      "```python\n"
      "compress_memory_file(path=\"$ARGUMENTS\", mode=\"$ARGUMENTS[1]\")\n"
      "```\n\n"
      "If no mode provided, call with `mode=\"full\"`.\n\n"
      "### Output\n"
      "- Report: ok/error, file path, backup path, and any warnings.\n"
    ),
  ),
}


def _parse_bool(v: str | None, default: bool) -> bool:
  if v is None:
    return default
  return str(v).strip().lower() in ("1", "true", "yes", "on")


def _split_list(v: str | None) -> list[str] | None:
  if v is None:
    return None
  s = str(v).strip()
  if not s:
    return None
  # Accept comma-separated or space-separated lists.
  if "," in s:
    parts = [p.strip() for p in s.split(",")]
  else:
    parts = [p.strip() for p in s.split()]
  out = [p for p in parts if p]
  return out or None


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
  """
  Minimal YAML-frontmatter parser sufficient for conventional SKILL.md.

  Supported:
  - key: value (single-line)
  - key: >
      multi-line
    (folded; newlines become spaces)
  - key: |
      multi-line
    (literal; newlines preserved)
  - key: [a, b]  (parsed as a string; caller can split if needed)

  Not supported:
  - nested objects
  - full YAML spec
  """
  m = _FRONTMATTER_RE.match(text or "")
  if not m:
    return {}, (text or "")
  raw = m.group(1)
  body = (text or "")[m.end() :]
  d: dict[str, str] = {}
  lines = raw.splitlines()
  i = 0
  while i < len(lines):
    line = lines[i]
    if not line.strip() or line.strip().startswith("#"):
      i += 1
      continue
    if ":" not in line:
      i += 1
      continue
    k, v = line.split(":", 1)
    k = k.strip()
    v = v.rstrip()
    v_stripped = v.strip()

    # Multi-line scalar blocks: `key: >` or `key: |` followed by indented lines.
    if v_stripped in (">", "|"):
      style = v_stripped
      block: list[str] = []
      i += 1
      while i < len(lines):
        nxt = lines[i]
        # YAML block scalars require indentation. We accept 2+ spaces or a tab.
        if not (nxt.startswith("  ") or nxt.startswith("\t")):
          break
        block.append(nxt.lstrip(" \t"))
        i += 1
      if style == ">":
        # folded: join non-empty lines with spaces; keep paragraph breaks as newline.
        paras: list[str] = []
        cur: list[str] = []
        for ln in block:
          if not ln.strip():
            if cur:
              paras.append(" ".join(x.strip() for x in cur if x.strip()))
              cur = []
            continue
          cur.append(ln)
        if cur:
          paras.append(" ".join(x.strip() for x in cur if x.strip()))
        d[k.lower()] = "\n".join(paras).strip()
      else:
        d[k.lower()] = "\n".join(block).rstrip()
      continue

    # Single-line scalar
    v2 = v_stripped
    # Strip simple surrounding quotes
    if len(v2) >= 2 and ((v2[0] == v2[-1] == '"') or (v2[0] == v2[-1] == "'")):
      v2 = v2[1:-1]
    d[k.lower()] = v2
    i += 1
  return d, body


def _is_valid_name(name: str) -> bool:
  return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name or ""))


def _skill_dirs_for_project(project_root: Path) -> list[Path]:
  """
  Discovery order (highest priority first):
  - project-local nested: walk up from project_root, collect `.gemcode/skills`
  - project-root: `.gemcode/skills`
  - personal: `~/.gemcode/skills`
  """
  out: list[Path] = []

  # Walk up (monorepo-style): closest first (higher priority).
  walk = project_root.resolve()
  cur = walk
  while True:
    d = cur / ".gemcode" / "skills"
    out.append(d)
    if cur == cur.parent:
      break
    cur = cur.parent
    if cur == Path.home() or cur == Path("/"):
      break

  # Personal (lowest priority)
  out.append(Path.home() / ".gemcode" / "skills")

  # De-dupe while preserving order
  seen: set[Path] = set()
  uniq: list[Path] = []
  for p in out:
    rp = p.resolve()
    if rp in seen:
      continue
    seen.add(rp)
    uniq.append(p)
  return uniq


def discover_skill_metas(project_root: Path) -> dict[str, tuple[GemSkillMeta, Path]]:
  """
  Returns name -> (meta, skill_dir). Higher-priority locations override lower ones.
  """
  found: dict[str, tuple[GemSkillMeta, Path]] = {}
  # Built-ins (lowest priority; anything on disk overrides).
  for k, (meta, _body) in _BUILTIN_SKILLS.items():
    found[k] = (meta, project_root)  # placeholder path; overridden by disk if present
  # We iterate low->high and overwrite so higher priority wins; but our list is high->low,
  # so invert it.
  dirs = list(reversed(_skill_dirs_for_project(project_root)))
  for base in dirs:
    if not base.is_dir():
      continue
    for child in base.iterdir():
      if not child.is_dir():
        continue
      skill_md = child / "SKILL.md"
      if not skill_md.is_file():
        continue
      try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
      except OSError:
        continue
      fm, body = _parse_frontmatter(text)
      name = (fm.get("name") or child.name).strip().lower()
      if not _is_valid_name(name):
        continue
      desc = (fm.get("description") or "").strip()
      if not desc:
        # fallback: first non-empty paragraph line from body
        desc = next((ln.strip() for ln in (body or "").splitlines() if ln.strip()), "")
      meta = GemSkillMeta(
        name=name,
        description=desc[:1024],
        disable_model_invocation=_parse_bool(fm.get("disable-model-invocation"), False),
        user_invocable=_parse_bool(fm.get("user-invocable"), True),
        allowed_tools=_split_list(fm.get("allowed-tools")),
        paths=_split_list(fm.get("paths")),
        context=(fm.get("context") or "").strip() or None,
        agent=(fm.get("agent") or "").strip() or None,
        model=(fm.get("model") or "").strip() or None,
      )
      found[name] = (meta, child)
  return found


def load_skill(project_root: Path, name: str) -> GemSkill | None:
  k = (name or "").strip().lower()
  # Built-in skills first (can be overridden by on-disk skills with same name).
  metas = discover_skill_metas(project_root)
  if k not in metas:
    return None
  meta, skill_dir = metas[k]

  if k in _BUILTIN_SKILLS and (skill_dir == project_root):
    _m, body = _BUILTIN_SKILLS[k]
    # synthetic paths
    return GemSkill(
      meta=meta,
      skill_dir=project_root,
      skill_md=project_root / f"<builtin:{k}>",
      body_markdown=body.strip(),
    )

  skill_md = skill_dir / "SKILL.md"
  try:
    text = skill_md.read_text(encoding="utf-8", errors="replace")
  except OSError:
    return None
  _, body = _parse_frontmatter(text)
  return GemSkill(meta=meta, skill_dir=skill_dir, skill_md=skill_md, body_markdown=body.strip())


def expand_skill_text(skill: GemSkill, *, arguments: str, session_id: str | None = None) -> str:
  args = (arguments or "").strip()
  argv = args.split() if args else []
  out = skill.body_markdown

  # ${...} substitutions
  out = out.replace("${GEMCODE_SESSION_ID}", str(session_id or ""))
  out = out.replace("${GEMCODE_SKILL_DIR}", str(skill.skill_dir))

  # $ARGUMENTS substitutions
  if "$ARGUMENTS" in out:
    # Replace indexed args first so `$ARGUMENTS[1]` doesn't get corrupted by replacing `$ARGUMENTS`.
    for i, a in enumerate(argv):
      out = out.replace(f"$ARGUMENTS[{i}]", a)
      out = out.replace(f"${i}", a)
    out = out.replace("$ARGUMENTS", args)
  else:
    if args:
      out = out + f"\n\nARGUMENTS: {args}\n"
  return out.strip()


def list_supporting_files(skill: GemSkill, *, max_items: int = 30) -> list[str]:
  out: list[str] = []
  try:
    for p in sorted(skill.skill_dir.rglob("*")):
      if p.is_dir():
        continue
      if p.name == "SKILL.md":
        continue
      rel = str(p.relative_to(skill.skill_dir))
      out.append(rel)
      if len(out) >= max_items:
        break
  except Exception:
    return []
  return out


def build_skill_manifest_text(project_root: Path) -> str:
  metas = discover_skill_metas(project_root)
  if not metas:
    return ""
  lines: list[str] = []
  lines.append("## GemSkills (optional, loaded on-demand)")
  lines.append("GemSkills are reusable playbooks stored in `.gemcode/skills/<name>/SKILL.md` or `~/.gemcode/skills/<name>/SKILL.md`.")
  lines.append("Only metadata is listed here; load full text when needed using `load_skill(name, arguments)`.")
  lines.append("")
  for name in sorted(metas.keys()):
    meta, _ = metas[name]
    inv = "manual-only" if meta.disable_model_invocation else "auto-eligible"
    lines.append(f"- **/{meta.name}** ({inv}): {meta.description}")
  return "\n".join(lines).strip()

