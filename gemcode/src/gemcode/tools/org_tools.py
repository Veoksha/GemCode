from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig
from gemcode.org import ensure_member_skill, find_member, hire_member, list_members, org_tree, resolve_fleet_root


def make_org_tools(cfg: GemCodeConfig) -> list:
  root = resolve_fleet_root(cfg.project_root)

  def _bus_enabled() -> bool:
    import os
    return os.environ.get("GEMCODE_ORG_BUS_REPORTS", "1").strip().lower() in (
      "1",
      "true",
      "yes",
      "on",
    )

  def _manager_address_for(m) -> str:
    # Default: manager is a virtual address for supervisor UIs.
    key = str(getattr(m, "reports_to", "") or "").strip()
    if not key or key.lower() == "manager":
      return "manager"
    boss = find_member(root, key)
    if boss is None:
      return "manager"
    try:
      addr = str(getattr(boss, "address", "") or "").strip()
      return addr or str(getattr(boss, "name", "") or "manager")
    except Exception:
      return "manager"

  def _ancestor_addresses_for(m) -> list[str]:
    """
    Return addresses for parent → grandparent → ... → manager (deduped).

    Uses `reports_to` chaining through org members. Falls back to "manager".
    """
    addrs: list[str] = []
    seen: set[str] = set()
    try:
      key = str(getattr(m, "reports_to", "") or "").strip()
      cur = key or "manager"
      hop = 0
      while hop < 16:  # hard cap to avoid cycles
        hop += 1
        if not cur or cur.lower() == "manager":
          if "manager" not in seen:
            addrs.append("manager")
            seen.add("manager")
          break
        boss = find_member(root, cur)
        if boss is None:
          if "manager" not in seen:
            addrs.append("manager")
            seen.add("manager")
          break
        addr = str(getattr(boss, "address", "") or getattr(boss, "name", "") or "").strip() or "manager"
        if addr not in seen:
          addrs.append(addr)
          seen.add(addr)
        # climb
        cur = str(getattr(boss, "reports_to", "") or "").strip() or "manager"
    except Exception:
      if "manager" not in seen:
        addrs.append("manager")
    return addrs or ["manager"]

  async def _publish_org_report(
    *,
    m,
    status: str,
    task: str,
    context: str,
    job_id: str = "",
    result: object | None = None,
    error: str = "",
  ) -> None:
    fleet_root = resolve_fleet_root(getattr(cfg, "project_root", Path.cwd()))

    def _audit_fallback(payload: dict[str, Any], *, why: str) -> None:
      try:
        from gemcode.audit import append_audit

        append_audit(
          fleet_root,
          {
            "event": "org.report",
            "why": why,
            "payload": payload,
          },
        )
      except Exception:
        return

    from_addr = str(getattr(m, "address", "") or getattr(m, "name", "") or "")
    member_dict = (m.to_dict() if hasattr(m, "to_dict") else {})
    # Capabilities snapshot: keep it small and stable.
    caps = {
      "kind": member_dict.get("kind"),
      "address": member_dict.get("address") or from_addr,
      "workspace_rel": member_dict.get("workspace_rel", ""),
      "reports_to": member_dict.get("reports_to", ""),
    }
    chain = _ancestor_addresses_for(m)
    payload: dict[str, Any] = {
      "member": member_dict,
      "capabilities": caps,
      "status": status,
      "task": task,
      "context": context,
      "job_id": job_id,
      "error": error,
      "result": result,
      "notify_chain": chain,
    }

    try:
      from gemcode.fleet_reports import maybe_append_org_report

      maybe_append_org_report(fleet_root, payload)
    except Exception:
      pass

    if not _bus_enabled():
      return

    sock = os.environ.get("GEMCODE_KAIRA_SOCKET") or str(fleet_root / ".gemcode" / "ipc.sock")
    # If runtime IPC isn't up, still persist the report locally.
    try:
      if not Path(sock).exists():
        _audit_fallback(payload, why="ipc_socket_missing")
        return
    except Exception:
      _audit_fallback(payload, why="ipc_socket_stat_failed")
      return

    try:
      from gemcode.kaira_client import KairaIpcClient

      c = await KairaIpcClient.connect(socket_path=str(sock))
      try:
        # Notify parent, grandparent, ... (and manager).
        for to_addr in chain:
          await c.publish(
            topic="org.report",
            to=str(to_addr or "manager"),
            from_addr=from_addr,
            payload=payload,
          )
      finally:
        await c.close()
    except Exception as e:
      _audit_fallback(payload, why=f"ipc_publish_failed: {type(e).__name__}: {e}")
      return

  def org_list() -> dict:
    """List available org members (workers)."""
    members = [m.to_dict() for m in list_members(root)]
    return {"ok": True, "members": members}

  def org_hire(
    name: str,
    title: str,
    kind: str = "subagent",
    address: str = "",
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
      address=str(address or "").strip(),
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
      # Delegate to Kaira via IPC enqueue (background). If IPC is unavailable,
      # fall back to an in-process subagent run so delegation still "just works".
      try:
        from gemcode.kaira_client import KairaIpcClient
        fleet_root = resolve_fleet_root(getattr(cfg, "project_root", Path.cwd()))
        sock_s = os.environ.get("GEMCODE_KAIRA_SOCKET") or str(fleet_root / ".gemcode" / "ipc.sock")
        client = await KairaIpcClient.connect(socket_path=sock_s)
        try:
          session_id = str(getattr(cfg, "_active_session_id", "") or "")
          notify_chain = _ancestor_addresses_for(m)
          # Attach org metadata so the runtime can auto-publish org.report when
          # the job finishes/fails (default, no extra prompting needed).
          meta = {
            "org": {
              "member": (m.to_dict() if hasattr(m, "to_dict") else {}),
              "capabilities": {
                "kind": getattr(m, "kind", ""),
                "address": getattr(m, "address", "") or getattr(m, "name", ""),
                "workspace_rel": getattr(m, "workspace_rel", "") or "",
                "reports_to": getattr(m, "reports_to", "") or "",
              },
              "task": task,
              "context": ctx,
              "notify_chain": notify_chain,
            }
          }
          res = await client.request(
            action="enqueue",
            prompt=prompt,
            priority=0,
            session_id=session_id,
            meta=meta,
          )
          if not res.get("ok"):
            await _publish_org_report(
              m=m,
              status="failed",
              task=task,
              context=ctx,
              error=str(res.get("error") or "enqueue_failed"),
            )
            return {"ok": False, "error": res.get("error") or "enqueue_failed"}
          job_id = str(res.get("job_id") or "")
          await _publish_org_report(
            m=m,
            status="delegated",
            task=task,
            context=ctx,
            job_id=job_id,
            result={"kind": "kaira_worker", "job_id": job_id},
          )
          return {"ok": True, "delegated_to": m.to_dict(), "job_id": job_id}
        finally:
          await client.close()
      except Exception as e:
        # Fallback: run as a subagent right now (best-effort). This keeps UX
        # consistent when users think "agent delegation" should always work.
        try:
          from gemcode.tools.subtask import make_run_subtask_tool

          run_subtask = make_run_subtask_tool(cfg)
          out = await run_subtask(prompt, "")
          result = out.get("result") if isinstance(out, dict) else out
          await _publish_org_report(
            m=m,
            status="finished",
            task=task,
            context=ctx,
            result={"kind": "fallback_subagent", "error": f"kaira_ipc_unavailable: {type(e).__name__}: {e}", "result": result},
          )
          return {"ok": True, "delegated_to": m.to_dict(), "result": result, "fallback": "subagent"}
        except Exception as e2:
          await _publish_org_report(
            m=m,
            status="failed",
            task=task,
            context=ctx,
            error=f"kaira_ipc_unavailable: {type(e).__name__}: {e}; fallback_subagent_failed: {type(e2).__name__}: {e2}",
          )
          return {"ok": False, "error": f"kaira_ipc_unavailable: {type(e).__name__}: {e}"}

    # Delegate to an in-process isolated subagent.
    try:
      from gemcode.tools.subtask import make_run_subtask_tool

      run_subtask = make_run_subtask_tool(cfg)
      out = await run_subtask(prompt, "")
      result = out.get("result") if isinstance(out, dict) else out
      await _publish_org_report(
        m=m,
        status="finished",
        task=task,
        context=ctx,
        result=result,
      )
      return {"ok": True, "delegated_to": m.to_dict(), "result": result}
    except Exception as e:
      await _publish_org_report(
        m=m,
        status="failed",
        task=task,
        context=ctx,
        error=f"subagent_failed: {type(e).__name__}: {e}",
      )
      return {"ok": False, "error": f"subagent_failed: {type(e).__name__}: {e}"}

  async def org_spawn(
    name: str,
    title: str,
    kind: str,
    task: str,
    address: str = "",
    reports_to: str = "manager",
    description: str = "",
    context: str = "",
  ) -> dict:
    """Hire a member and immediately delegate a task to them."""
    h = org_hire(
      name=name,
      title=title,
      kind=kind,
      address=address,
      reports_to=reports_to,
      description=description,
    )
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

