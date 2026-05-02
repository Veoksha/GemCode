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


def resolve_fleet_root(start_root: Path) -> Path:
  """
  Resolve the "fleet root" for org/agent operations.

  When GemCode is run from an agent workspace (e.g. `.gemcode/agents/<id>-<slug>`),
  we still want org tools to operate on the shared `.gemcode/org.json` at the
  parent project root.

  Strategy: walk up ancestors until we find `.gemcode/org.json`. If none is found,
  fall back to the provided start_root.
  """
  try:
    cur = start_root.resolve()
  except Exception:
    cur = start_root
  try:
    while True:
      if (cur / ".gemcode" / "org.json").is_file():
        return cur
      if cur == cur.parent:
        break
      nxt = cur.parent
      cur = nxt
  except Exception:
    return start_root
  return start_root


@dataclass
class OrgMember:
  id: str
  name: str
  title: str
  kind: MemberKind
  address: str = ""  # stable bus address (defaults to name)
  workspace_rel: str = ""  # agent workspace directory relative to project root
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
      "address": self.address or self.name,
      "workspace_rel": self.workspace_rel,
      "reports_to": self.reports_to,
      "skill_name": self.skill_name,
      "description": self.description,
      "created_ms": int(self.created_ms or 0),
    }


def agents_root(project_root: Path) -> Path:
  return project_root / ".gemcode" / "agents"


def _agent_dir_name(member: OrgMember) -> str:
  slug = _skill_slug(member.name)
  mid = (member.id or "m").strip()
  return f"{mid}-{slug}" if slug else mid


def ensure_agent_workspace(project_root: Path, *, member: OrgMember) -> str:
  """
  Ensure a per-agent workspace exists under `.gemcode/agents/`.

  Returns the `workspace_rel` path that should be stored on the member.
  """
  base = agents_root(project_root)
  base.mkdir(parents=True, exist_ok=True)
  rel = member.workspace_rel.strip() if member.workspace_rel else ""
  if not rel:
    rel = str(Path(".gemcode") / "agents" / _agent_dir_name(member))
  ws = project_root / rel
  ws.mkdir(parents=True, exist_ok=True)
  # Ensure agent-local `.gemcode/` exists (so agent-local skills can live here
  # when you run GemCode with `-C <agent_workspace>`).
  try:
    (ws / ".gemcode").mkdir(parents=True, exist_ok=True)
  except Exception:
    pass

  # Optional agent "constitution" workspace. When you run `gemcode -C <ws>`,
  # GemCode can assemble these files into a stable, ordered prompt section.
  try:
    wdir = ws / "workspace"
    wdir.mkdir(parents=True, exist_ok=True)

    templates: list[tuple[str, str]] = [
      (
        "GOALS.md",
        "# Goals\n\n"
        "List a few durable goals for this agent.\n\n"
        "- (example) Keep outputs concise and decision-ready.\n"
        "- (example) Prefer evidence over assumptions.\n",
      ),
      (
        "POLICIES.md",
        "# Policies\n\n"
        "Rules and constraints this agent must follow.\n\n"
        "- (example) Do not run destructive commands without explicit approval.\n",
      ),
      (
        "SKILLS.md",
        "# Skills\n\n"
        "High-level skill modules for this agent (optional).\n\n"
        "- Keep this short; put larger modules under `workspace/skills/<name>/SKILL.md`.\n",
      ),
      (
        "HEARTBEAT.md",
        "# Heartbeat\n\n"
        "Checklist for periodic/self-initiated work (optional).\n\n"
        "- (example) Summarize recent runtime events.\n"
        "- (example) Check for failing jobs and propose fixes.\n",
      ),
    ]
    for fn, body in templates:
      p = wdir / fn
      if not p.exists():
        p.write_text(body, encoding="utf-8")

    (wdir / "skills").mkdir(parents=True, exist_ok=True)
  except Exception:
    pass
  # Minimal marker file for humans.
  try:
    readme = ws / "README.md"
    if not readme.exists():
      readme.write_text(
        f"# GemCode agent workspace: {member.name}\n\n"
        f"- id: `{member.id}`\n"
        f"- address: `{member.address or member.name}`\n"
        f"- reports_to: `{member.reports_to or 'manager'}`\n\n"
        "This directory is a per-agent workspace. You can run GemCode here with:\n\n"
        f"```bash\ngemcode -C \"{ws}\"\n```\n",
        encoding="utf-8",
      )
  except Exception:
    pass

  # Agent metadata file (description optional; can be edited later).
  try:
    agent_md = ws / "AGENT.md"
    if not agent_md.exists():
      desc = (member.description or "").strip()
      if not desc:
        desc = "(no description yet — add later with: /agent describe <name|id> <text...>)"
      agent_md.write_text(
        "# Agent\n\n"
        f"- **name**: `{member.name}`\n"
        f"- **id**: `{member.id}`\n"
        f"- **title**: `{member.title}`\n"
        f"- **kind**: `{member.kind}`\n"
        f"- **address**: `{member.address or member.name}`\n"
        f"- **reports_to**: `{member.reports_to or 'manager'}`\n\n"
        "## Description\n"
        f"{desc}\n\n"
        "## Local skills\n"
        "Agent-local GemSkills live under this workspace at:\n"
        "- `.gemcode/skills/`\n",
        encoding="utf-8",
      )
  except Exception:
    pass

  return rel


def ensure_agent_local_skill(project_root: Path, *, member: OrgMember) -> str | None:
  """
  Create an agent-local GemSkill inside the agent workspace.

  This is *not* the same as org member skills under the main project root.
  When you run `gemcode -C <agent_workspace>`, this local skill becomes visible
  only to that agent.
  """
  try:
    ws_rel = (member.workspace_rel or "").strip()
    if not ws_rel:
      return None
    ws = (project_root / ws_rel).resolve()
    skill_name = f"agent-{_skill_slug(member.name)}"
    skill_dir = ws / ".gemcode" / "skills" / skill_name
    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists():
      return skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    desc = (member.description or "").strip() or f"Local skill for agent {member.name}."
    body = (
      f"---\n"
      f"name: {skill_name}\n"
      f"description: >\n"
      f"  {desc}\n"
      f"user-invocable: false\n"
      f"disable-model-invocation: false\n"
      f"context: agent\n"
      f"agent: {member.name}\n"
      f"---\n\n"
      f"## Role\n"
      f"You are **{member.name}**.\n"
      f"- **Title**: {member.title}\n"
      f"- **Reports to**: {member.reports_to or 'manager'}\n"
      f"- **Kind**: {member.kind}\n\n"
      f"## Working style\n"
      f"- Keep outputs concise.\n"
      f"- Prefer evidence and small steps.\n"
    )
    skill_md.write_text(body, encoding="utf-8")
    return skill_name
  except Exception:
    return None


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
        "address": "kaira",
        "workspace_rel": "",
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
        "address": "verifier",
        "workspace_rel": "",
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
  root = resolve_fleet_root(project_root)
  org = load_org(root)
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
          address=str(m.get("address") or m.get("name") or ""),
          workspace_rel=str(m.get("workspace_rel") or ""),
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
  address: str = "",
  workspace_rel: str = "",
  reports_to: str = "manager",
  description: str = "",
) -> OrgMember:
  root = resolve_fleet_root(project_root)
  org = load_org(root)
  members = list(org.get("members") or [])

  # Prevent duplicate names — ADK sub_agents require unique names
  clean_name = name.strip().lower()
  for existing in members:
    if str(existing.get("name", "")).strip().lower() == clean_name:
      # Return the existing member instead of creating a duplicate
      return OrgMember(
        id=str(existing.get("id") or ""),
        name=str(existing.get("name") or ""),
        title=str(existing.get("title") or title),
        kind=str(existing.get("kind") or kind),
        address=str(existing.get("address") or ""),
        workspace_rel=str(existing.get("workspace_rel") or ""),
        reports_to=str(existing.get("reports_to") or ""),
        skill_name=str(existing.get("skill_name") or ""),
        description=str(existing.get("description") or description),
        created_ms=int(existing.get("created_ms") or 0),
      )

  now = _now_ms()
  mid = f"m_{uuid.uuid4().hex[:10]}"
  m = OrgMember(
    id=mid,
    name=name.strip(),
    title=title.strip(),
    kind=kind,
    address=(address or "").strip(),
    workspace_rel=(workspace_rel or "").strip(),
    reports_to=(reports_to or "").strip(),
    skill_name="",
    description=(description or "").strip(),
    created_ms=now,
  )
  # Always create an agent workspace (lightweight; safe).
  try:
    m.workspace_rel = ensure_agent_workspace(root, member=m)
  except Exception:
    pass
  # Auto-create a role GemSkill for this member (optional, default on).
  try:
    import os
    if os.environ.get("GEMCODE_ORG_AUTO_SKILLS", "1").strip().lower() in ("1", "true", "yes", "on"):
      skill = ensure_member_skill(root, member=m)
      if skill:
        m.skill_name = skill
  except Exception:
    pass
  members.append(m.to_dict())
  org["members"] = members
  save_org(root, org)
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

