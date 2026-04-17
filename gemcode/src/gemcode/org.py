from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

MemberKind = Literal["kaira_worker", "subagent"]


def _now_ms() -> int:
  return int(time.time() * 1000)


def org_path(project_root: Path) -> Path:
  return project_root / ".gemcode" / "org.json"


@dataclass
class OrgMember:
  id: str
  name: str
  title: str
  kind: MemberKind
  reports_to: str = ""  # member id or name
  skill_name: str = ""  # kebab-case GemSkill name, if created
  description: str = ""
  created_ms: int = 0

  def to_dict(self) -> dict[str, Any]:
    return {
      "id": self.id,
      "name": self.name,
      "title": self.title,
      "kind": self.kind,
      "reports_to": self.reports_to,
      "skill_name": self.skill_name,
      "description": self.description,
      "created_ms": int(self.created_ms or 0),
    }


def _default_org() -> dict[str, Any]:
  # Minimal defaults: manager exists implicitly; provide a few standard workers.
  now = _now_ms()
  return {
    "version": 1,
    "created_ms": now,
    "members": [
      {
        "id": "m_kaira",
        "name": "kaira",
        "title": "BackgroundWorker",
        "kind": "kaira_worker",
        "reports_to": "manager",
        "skill_name": "member-kaira",
        "description": "Runs background jobs (tests/lint/scans) and reports back.",
        "created_ms": now,
      },
      {
        "id": "m_verifier",
        "name": "verifier",
        "title": "Verifier",
        "kind": "subagent",
        "reports_to": "manager",
        "skill_name": "member-verifier",
        "description": "Independent review / sanity checks on proposed changes.",
        "created_ms": now,
      },
    ],
  }


def load_org(project_root: Path) -> dict[str, Any]:
  p = org_path(project_root)
  if not p.exists():
    return _default_org()
  try:
    obj = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
      return _default_org()
    obj.setdefault("version", 1)
    obj.setdefault("members", [])
    if not isinstance(obj.get("members"), list):
      obj["members"] = []
    return obj
  except Exception:
    return _default_org()


def save_org(project_root: Path, org: dict[str, Any]) -> None:
  p = org_path(project_root)
  p.parent.mkdir(parents=True, exist_ok=True)
  tmp = p.with_suffix(".json.tmp")
  tmp.write_text(json.dumps(org, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
  tmp.replace(p)


def list_members(project_root: Path) -> list[OrgMember]:
  org = load_org(project_root)
  out: list[OrgMember] = []
  for m in org.get("members") or []:
    if not isinstance(m, dict):
      continue
    try:
      out.append(
        OrgMember(
          id=str(m.get("id") or ""),
          name=str(m.get("name") or ""),
          title=str(m.get("title") or ""),
          kind=str(m.get("kind") or "subagent"),  # type: ignore[arg-type]
          reports_to=str(m.get("reports_to") or ""),
          skill_name=str(m.get("skill_name") or ""),
          description=str(m.get("description") or ""),
          created_ms=int(m.get("created_ms") or 0),
        )
      )
    except Exception:
      continue
  return [m for m in out if m.id and m.name and m.title]


def hire_member(
  project_root: Path,
  *,
  name: str,
  title: str,
  kind: MemberKind,
  reports_to: str = "manager",
  description: str = "",
) -> OrgMember:
  org = load_org(project_root)
  members = list(org.get("members") or [])
  now = _now_ms()
  mid = f"m_{uuid.uuid4().hex[:10]}"
  m = OrgMember(
    id=mid,
    name=name.strip(),
    title=title.strip(),
    kind=kind,
    reports_to=(reports_to or "").strip(),
    skill_name="",
    description=(description or "").strip(),
    created_ms=now,
  )
  # Auto-create a role GemSkill for this member (optional, default on).
  try:
    import os
    if os.environ.get("GEMCODE_ORG_AUTO_SKILLS", "1").strip().lower() in ("1", "true", "yes", "on"):
      skill = ensure_member_skill(project_root, member=m)
      if skill:
        m.skill_name = skill
  except Exception:
    pass
  members.append(m.to_dict())
  org["members"] = members
  save_org(project_root, org)
  return m


def find_member(project_root: Path, member_id_or_name: str) -> OrgMember | None:
  key = (member_id_or_name or "").strip().lower()
  if not key:
    return None
  for m in list_members(project_root):
    if m.id.lower() == key or m.name.lower() == key:
      return m
  return None


def org_tree(project_root: Path) -> list[dict[str, Any]]:
  """Return a simple manager-centric hierarchy tree."""
  ms = list_members(project_root)
  by_key: dict[str, OrgMember] = {}
  for m in ms:
    by_key[m.id.lower()] = m
    by_key[m.name.lower()] = m

  def parent_key(m: OrgMember) -> str:
    k = (m.reports_to or "manager").strip().lower()
    return k or "manager"

  children: dict[str, list[OrgMember]] = {}
  for m in ms:
    pk = parent_key(m)
    children.setdefault(pk, []).append(m)

  def node_for(key: str) -> dict[str, Any]:
    if key == "manager":
      kids = children.get("manager", [])
      return {"name": "manager", "title": "Manager", "kind": "manager", "reports": [n for n in (node_for(m.id.lower()) for m in sorted(kids, key=lambda x: x.name.lower()))]}
    m = by_key.get(key)
    if m is None:
      return {"name": key, "title": "Unknown", "kind": "unknown", "reports": []}
    kids = children.get(m.id.lower(), []) + children.get(m.name.lower(), [])
    # Dedup kids
    seen = set()
    uniq: list[OrgMember] = []
    for c in kids:
      if c.id in seen:
        continue
      seen.add(c.id)
      uniq.append(c)
    return {
      "id": m.id,
      "name": m.name,
      "title": m.title,
      "kind": m.kind,
      "reports_to": m.reports_to,
      "reports": [node_for(c.id.lower()) for c in sorted(uniq, key=lambda x: x.name.lower())],
    }

  return [node_for("manager")]


def _skill_slug(name: str) -> str:
  # Skills expect [a-z0-9-]{1,64}
  import re

  base = (name or "").strip().lower()
  base = re.sub(r"[^a-z0-9]+", "-", base)
  base = base.strip("-")
  if not base:
    base = "member"
  return base[:48]


def ensure_member_skill(project_root: Path, *, member: OrgMember) -> str | None:
  """Create a GemSkill for a member if missing; return skill name."""
  import os

  if os.environ.get("GEMCODE_ORG_AUTO_SKILLS", "1").strip().lower() not in ("1", "true", "yes", "on"):
    return None

  skill_name = member.skill_name.strip().lower() if member.skill_name else ""
  if not skill_name:
    skill_name = f"member-{_skill_slug(member.name)}"

  skill_dir = project_root / ".gemcode" / "skills" / skill_name
  skill_md = skill_dir / "SKILL.md"
  if skill_md.exists():
    return skill_name

  skill_dir.mkdir(parents=True, exist_ok=True)

  desc = f"Operating procedure for {member.name} ({member.title})."
  body = (
    f"---\n"
    f"name: {skill_name}\n"
    f"description: >\n"
    f"  {desc}\n"
    f"user-invocable: false\n"
    f"disable-model-invocation: false\n"
    f"context: org\n"
    f"agent: {member.name}\n"
    f"---\n\n"
    f"## Role\n"
    f"You are **{member.name}**.\n"
    f"- **Title**: {member.title}\n"
    f"- **Reports to**: {member.reports_to or 'manager'}\n"
    f"- **Kind**: {member.kind}\n"
    f"- **Description**: {member.description or '(none)'}\n\n"
    f"## Default workflow\n"
    f"1. Restate the task in your own words.\n"
    f"2. Gather evidence with the smallest set of tools needed.\n"
    f"3. Produce a concise, decision-ready report.\n"
    f"4. If blocked, propose 1–3 options and ask the manager for a decision.\n\n"
    f"## Output contract\n"
    f"Return results as **STRICT JSON** (preferred), otherwise use the markdown format below.\n\n"
    f"### JSON schema (preferred)\n"
    f"{{\n"
    f"  \"status\": \"pass|fail|blocked\",\n"
    f"  \"summary\": [\"...\"],\n"
    f"  \"evidence\": [\"paths/commands/ids\"],\n"
    f"  \"recommended_next_actions\": [\"...\"],\n"
    f"  \"notes\": \"optional\"\n"
    f"}}\n\n"
    f"### Markdown fallback\n"
    f"### Summary\n"
    f"- 3–7 bullets\n\n"
    f"### Evidence\n"
    f"- File paths / commands / IDs you used\n\n"
    f"### Recommendation\n"
    f"- What the manager should do next (1–3 options)\n\n"
    f"## Safety + escalation\n"
    f"- Do not do destructive actions unless explicitly approved.\n"
    f"- If a tool asks for confirmation, wait for approval.\n"
    f"- If you need more scope, ask the manager to delegate or spawn another member.\n\n"
    f"## Invocation\n"
    f"- This skill may be loaded with: `load_skill(\"{skill_name}\")`\n"
  )
  skill_md.write_text(body, encoding="utf-8")
  return skill_name

