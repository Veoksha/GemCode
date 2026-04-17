from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig
from gemcode.org import ensure_member_skill, find_member, hire_member, list_members, org_tree


def make_org_tools(cfg: GemCodeConfig) -> list:
  root = cfg.project_root

  def org_list() -> dict:
    """List available org members (workers)."""
    members = [m.to_dict() for m in list_members(root)]
    return {"ok": True, "members": members}

  def org_hire(
    name: str,
    title: str,
    kind: str = "subagent",
    reports_to: str = "manager",
    description: str = "",
  ) -> dict:
    """Hire a new org member (persistent under .gemcode/org.json)."""
    k = (kind or "subagent").strip().lower()
    if k not in ("kaira_worker", "subagent"):
      return {"ok": False, "error": "kind must be kaira_worker or subagent"}
    m = hire_member(
      root,
      name=str(name or "").strip(),
      title=str(title or "").strip(),
      kind=k,  # type: ignore[arg-type]
      reports_to=str(reports_to or "manager").strip(),
      description=str(description or "").strip(),
    )
    # Ensure skill exists even if env toggles changed between calls.
    try:
      ensure_member_skill(root, member=m)
    except Exception:
      pass
    return {"ok": True, "member": m.to_dict()}

  def org_tree_view() -> dict:
    """Show org hierarchy (manager → reports)."""
    return {"ok": True, "tree": org_tree(root)}

  async def org_delegate(member: str, task: str, context: str = "") -> dict:
    """Delegate a task to an org member (Kaira worker or subagent)."""
    m = find_member(root, member)
    if m is None:
      return {"ok": False, "error": f"unknown member: {member}"}

    task = (task or "").strip()
    ctx = (context or "").strip()
    if not task:
      return {"ok": False, "error": "missing task"}

    header = (
      f"You are {m.name} ({m.title}).\n"
      f"Role description: {m.description or '(none)'}\n\n"
      "Before acting, load and follow your role skill if available.\n"
      f"- If a GemSkill exists: call load_skill(\"{m.skill_name or 'member-' + m.name.lower()}\")\n\n"
      "Do the assigned task. Keep outputs concise and actionable.\n"
    )
    prompt = header + "\nTask:\n" + task
    if ctx:
      prompt += "\n\nContext:\n" + ctx

    if m.kind == "kaira_worker":
      # Delegate to Kaira via IPC enqueue (background).
      try:
        from gemcode.kaira_client import KairaIpcClient
        sock = (
          getattr(cfg, "project_root", Path.cwd()) / ".gemcode" / "ipc.sock"
        )
        sock_s = str(sock)
        client = await KairaIpcClient.connect(socket_path=sock_s)
        try:
          session_id = str(getattr(cfg, "_active_session_id", "") or "")
          res = await client.request(
            action="enqueue",
            prompt=prompt,
            priority=0,
            session_id=session_id,
          )
          if not res.get("ok"):
            return {"ok": False, "error": res.get("error") or "enqueue_failed"}
          return {"ok": True, "delegated_to": m.to_dict(), "job_id": res.get("job_id")}
        finally:
          await client.close()
      except Exception as e:
        return {"ok": False, "error": f"kaira_ipc_unavailable: {type(e).__name__}: {e}"}

    # Delegate to an in-process isolated subagent.
    try:
      from gemcode.tools.subtask import make_run_subtask_tool

      run_subtask = make_run_subtask_tool(cfg)
      out = await run_subtask(prompt, "")
      return {"ok": True, "delegated_to": m.to_dict(), "result": out.get("result") if isinstance(out, dict) else out}
    except Exception as e:
      return {"ok": False, "error": f"subagent_failed: {type(e).__name__}: {e}"}

  async def org_spawn(
    name: str,
    title: str,
    kind: str,
    task: str,
    reports_to: str = "manager",
    description: str = "",
    context: str = "",
  ) -> dict:
    """Hire a member and immediately delegate a task to them."""
    h = org_hire(name=name, title=title, kind=kind, reports_to=reports_to, description=description)
    if not h.get("ok"):
      return h
    mem = (h.get("member") or {}) if isinstance(h.get("member"), dict) else {}
    member_key = str(mem.get("id") or mem.get("name") or name)
    d = await org_delegate(member=member_key, task=task, context=context)
    return {"ok": bool(d.get("ok")), "member": mem, "delegation": d}

  def org_improve(member: str, lessons: str) -> dict:
    """Append improvements to a member's skill so future delegations perform better."""
    m = find_member(root, member)
    if m is None:
      return {"ok": False, "error": f"unknown member: {member}"}
    skill = ensure_member_skill(root, member=m)
    if not skill:
      return {"ok": False, "error": "skill_creation_disabled"}
    skill_md = root / ".gemcode" / "skills" / skill / "SKILL.md"
    if not skill_md.exists():
      return {"ok": False, "error": "skill_missing"}
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    block = (lessons or "").strip()
    if not block:
      return {"ok": False, "error": "missing lessons"}
    import time
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    addition = (
      "\n\n"
      "## Improvements\n"
      f"- Added: {ts}\n"
      f"- Notes:\n{block}\n"
    )
    skill_md.write_text(text + addition, encoding="utf-8")
    return {"ok": True, "skill_name": skill, "skill_path": str(skill_md)}

  org_list.__name__ = "org_list"
  org_hire.__name__ = "org_hire"
  org_tree_view.__name__ = "org_tree"
  org_delegate.__name__ = "org_delegate"
  org_spawn.__name__ = "org_spawn"
  org_improve.__name__ = "org_improve"

  return [org_list, org_hire, org_tree_view, org_delegate, org_spawn, org_improve]

