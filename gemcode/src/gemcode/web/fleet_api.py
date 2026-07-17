"""Read-only fleet/org/habits snapshots for the GemCode web API."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from gemcode.agent_habits import Habit, load_habits, save_habits
from gemcode.fleet_reports import has_pending_fleet_reports, preview_fleet_inbox
from gemcode.org import ensure_member_skill, hire_member, list_members, org_tree, resolve_fleet_root


from gemcode.web.project_root import resolve_web_project_root


def _resolve_root(raw_path: str | None) -> Path:
  return resolve_web_project_root(raw_path)


def _habits_root(project_root: Path) -> Path:
  return resolve_fleet_root(project_root)


def _mesh_thread_running(mesh: Any) -> bool:
  try:
    bg = getattr(mesh, "_bg_thread", None)
    return bg is not None and bg.is_alive()
  except Exception:
    return False


def ensure_web_mesh_running(project_root: Path, *, autostart: bool = True) -> dict[str, Any]:
  """Start the in-process agent mesh so scheduled habits can fire in the web API."""
  fleet_root = _habits_root(project_root)
  try:
    from gemcode.config import GemCodeConfig, load_cli_environment
    from gemcode.agent_mesh import ensure_mesh

    load_cli_environment()
    cfg = GemCodeConfig(project_root=fleet_root)
    mesh = ensure_mesh(cfg)
    if autostart and not _mesh_thread_running(mesh):
      mesh.start()
      mesh._wait_bg_loop_ready(timeout_s=5.0)
    thread_running = _mesh_thread_running(mesh)
    return {
      "ok": thread_running,
      "thread_running": thread_running,
      "fleet_root": str(fleet_root),
      **mesh.status(),
    }
  except Exception as exc:
    return {
      "ok": False,
      "thread_running": False,
      "fleet_root": str(fleet_root),
      "error": f"{type(exc).__name__}: {exc}",
    }


def _habit_schedule_label(h: dict[str, Any]) -> str:
  after = str(h.get("trigger_after") or "").strip()
  if after:
    on = str(h.get("trigger_on") or "finished").strip().lower()
    if on in ("any", "*"):
      on_label = "any outcome"
    elif on == "failed":
      on_label = "on failure"
    else:
      on_label = "on success"
    return f"After `{after}` ({on_label})"
  if h.get("daily_at"):
    return f"Daily at {h['daily_at']}"
  if h.get("cron"):
    return f"Cron {h['cron']}"
  secs = h.get("every_seconds")
  if isinstance(secs, int) and secs > 0:
    if secs % 3600 == 0:
      return f"Every {secs // 3600}h"
    if secs % 60 == 0:
      return f"Every {secs // 60}m"
    return f"Every {secs}s"
  return "No schedule"


def org_snapshot(project_root: Path) -> dict[str, Any]:
  fleet_root = resolve_fleet_root(project_root)
  org_file = fleet_root / ".gemcode" / "org.json"
  members = [m.to_dict() for m in list_members(project_root)]
  tree = org_tree(project_root)
  return {
    "ok": True,
    "fleet_root": str(fleet_root),
    "org_file": str(org_file),
    "org_exists": org_file.is_file(),
    "member_count": len(members),
    "members": members,
    "tree": tree,
  }


def habits_snapshot(project_root: Path) -> dict[str, Any]:
  fleet_root = _habits_root(project_root)
  habits_path = fleet_root / ".gemcode" / "habits.json"
  habits = load_habits(fleet_root)
  rows: list[dict[str, Any]] = []
  for h in habits:
    d = h.to_dict()
    d["schedule"] = _habit_schedule_label(d)
    rows.append(d)
  enabled = sum(1 for h in habits if h.enabled)
  scheduler = ensure_web_mesh_running(project_root, autostart=enabled > 0)
  return {
    "ok": True,
    "fleet_root": str(fleet_root),
    "habits_file": str(habits_path),
    "habits_exists": habits_path.is_file(),
    "total": len(rows),
    "enabled": enabled,
    "paused": len(rows) - enabled,
    "habits": rows,
    "scheduler": scheduler,
  }


def _recent_fleet_records(fleet_root: Path, *, limit: int = 25) -> list[dict[str, Any]]:
  p = fleet_root / ".gemcode" / "fleet_reports.jsonl"
  if not p.is_file():
    return []
  try:
    lines = [ln.strip() for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
  except OSError:
    return []
  out: list[dict[str, Any]] = []
  for line in lines[-limit:]:
    try:
      rec = json.loads(line)
    except json.JSONDecodeError:
      continue
    if isinstance(rec, dict):
      out.append(rec)
  return out


def mesh_snapshot(project_root: Path) -> dict[str, Any]:
  fleet_root = _habits_root(project_root)
  pending = has_pending_fleet_reports(project_root)
  preview = preview_fleet_inbox(project_root, max_chars=8000)
  recent = _recent_fleet_records(fleet_root)

  habits = load_habits(fleet_root)
  habits_enabled = sum(1 for h in habits if h.enabled)

  live: dict[str, Any] | None = None
  mesh_note = (
    "Fleet inbox and habits reflect on-disk state at the fleet root. "
    "Start the API server to run scheduled tasks."
  )
  if habits_enabled > 0:
    live = ensure_web_mesh_running(project_root)
    if live.get("thread_running"):
      mesh_note = (
        "Mesh scheduler is running in this API process. "
        "Active schedules fire while the server is up."
      )
    else:
      mesh_note = (
        "Active schedules exist but the mesh scheduler is not running — "
        "check API logs or restart the server."
      )
  else:
    try:
      from gemcode.config import GemCodeConfig
      from gemcode.agent_mesh import get_mesh

      cfg = GemCodeConfig(project_root=fleet_root)
      mesh = get_mesh(cfg)
      if mesh is not None:
        thread_running = _mesh_thread_running(mesh)
        live = {"ok": thread_running, "thread_running": thread_running, **mesh.status()}
        if thread_running:
          mesh_note = "Mesh scheduler is running in this API process."
      else:
        live = {"ok": False, "thread_running": False, "error": "mesh not initialized in this process"}
    except Exception as exc:
      live = {"ok": False, "thread_running": False, "error": f"{type(exc).__name__}: {exc}"}
  return {
    "ok": True,
    "fleet_root": str(fleet_root),
    "pending_reports": pending,
    "inbox_preview": preview,
    "recent_reports": recent,
    "habits_enabled": sum(1 for h in habits if h.enabled),
    "habits_total": len(habits),
    "live_mesh": live,
    "note": mesh_note,
  }


def mesh_halt_action(project_root: Path, *, clear_habits: bool = False) -> dict[str, Any]:
  try:
    from gemcode.config import GemCodeConfig
    from gemcode.agent_mesh import get_mesh

    fleet_root = _habits_root(project_root)
    cfg = GemCodeConfig(project_root=fleet_root)
    mesh = get_mesh(cfg)
    if mesh is None:
      return {"ok": False, "error": "mesh not initialized in this API process"}
    result = mesh.halt_jobs(clear_queue=True, cancel_running=True)
    payload: dict[str, Any] = {
      "ok": True,
      "cleared_queued": result.get("cleared_queued", 0),
      "cancelled_running": result.get("cancelled_running", 0),
    }
    if clear_habits:
      save_habits(fleet_root, [])
      payload["habits_cleared"] = True
    return payload
  except Exception as exc:
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def _org_trigger_async(
  project_root: Path,
  *,
  member: str,
  task: str,
  session_id: str = "",
) -> dict[str, Any]:
  from gemcode.kaira_client import KairaIpcClient
  from gemcode.kaira_ipc import fleet_manager_ipc_path
  from gemcode.org import resolve_fleet_root

  fleet_root = resolve_fleet_root(project_root)
  sock = str(fleet_manager_ipc_path(fleet_root))
  client = await KairaIpcClient.connect(socket_path=sock)
  try:
    res = await client.publish(
      topic="org.assign",
      payload={"member": member, "task": task, "context": "", "session_id": session_id},
      to="manager",
      from_addr="gemcode-web",
    )
  finally:
    await client.close()
  if not res.get("ok"):
    return {"ok": False, "error": str(res.get("error") or "publish_failed")}
  return {"ok": True, "member": member, "via": "runtime"}


def org_trigger_action(
  project_root: Path,
  *,
  member: str,
  task: str,
  session_id: str = "",
) -> dict[str, Any]:
  import asyncio

  nm = (member or "").strip()
  tk = (task or "").strip()
  if not nm:
    return {"ok": False, "error": "member is required"}
  if not tk:
    return {"ok": False, "error": "task is required"}

  try:
    return asyncio.run(
      _org_trigger_async(project_root, member=nm, task=tk, session_id=session_id)
    )
  except Exception as exc:
    try:
      from gemcode.config import GemCodeConfig
      from gemcode.tools.org_tools import make_org_tools

      cfg = GemCodeConfig(project_root=project_root)
      org_delegate = None
      for t in make_org_tools(cfg):
        if getattr(t, "__name__", "") == "org_delegate":
          org_delegate = t
          break
      if org_delegate is None:
        return {"ok": False, "error": f"runtime unavailable ({exc}); org_delegate missing"}
      result = org_delegate(member=nm, task=tk)
      return {"ok": True, "member": nm, "via": "org_delegate", "result": str(result)}
    except Exception as exc2:
      return {"ok": False, "error": f"{type(exc).__name__}: {exc}; fallback: {exc2}"}


def habits_action(project_root: Path, *, action: str, name: str) -> dict[str, Any]:
  nm = (name or "").strip().lower()
  if not nm:
    return {"ok": False, "error": "name is required"}

  fleet_root = _habits_root(project_root)
  habits = load_habits(fleet_root)
  if action == "remove":
    before = len(habits)
    habits = [h for h in habits if h.name != nm]
    save_habits(fleet_root, habits)
    return {"ok": True, "removed": before - len(habits)}

  for h in habits:
    if h.name == nm:
      if action == "pause":
        h.enabled = False
      elif action == "resume":
        h.enabled = True
      else:
        return {"ok": False, "error": f"unknown action: {action}"}
      save_habits(fleet_root, habits)
      scheduler = ensure_web_mesh_running(project_root, autostart=h.enabled)
      return {"ok": True, "name": h.name, "enabled": h.enabled, "scheduler": scheduler}

  return {"ok": False, "error": f"habit not found: {name}"}


def org_hire_action(
  project_root: Path,
  *,
  name: str,
  title: str,
  kind: str = "subagent",
  description: str = "",
  reports_to: str = "manager",
) -> dict[str, Any]:
  import re

  nm = (name or "").strip().lower()
  if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", nm):
    return {
      "ok": False,
      "error": "name must be lowercase letters, numbers, dashes (start with a letter, max 32)",
    }
  if not (title or "").strip():
    return {"ok": False, "error": "title is required"}

  k = (kind or "subagent").strip().lower()
  if k not in ("kaira_worker", "subagent"):
    return {"ok": False, "error": "kind must be kaira_worker or subagent"}

  try:
    m = hire_member(
      project_root,
      name=nm,
      title=title.strip(),
      kind=k,  # type: ignore[arg-type]
      reports_to=(reports_to or "manager").strip(),
      description=(description or "").strip(),
    )
    try:
      ensure_member_skill(resolve_fleet_root(project_root), member=m)
    except Exception:
      pass
    return {"ok": True, "member": m.to_dict()}
  except Exception as exc:
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def habits_add_action(
  project_root: Path,
  *,
  name: str,
  agent: str,
  prompt: str,
  every_minutes: int = 0,
  daily_at: str = "",
  cron: str = "",
  trigger_after: str = "",
  trigger_on: str = "finished",
  trigger_cooldown_s: float = 0,
) -> dict[str, Any]:
  import re

  nm = (name or "").strip().lower()
  if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", nm):
    return {"ok": False, "error": "invalid name (lowercase, numbers, dashes, max 64 chars)"}
  if not (agent or "").strip():
    return {"ok": False, "error": "agent is required"}
  if not (prompt or "").strip():
    return {"ok": False, "error": "prompt is required"}

  fleet_root = _habits_root(project_root)
  habits = load_habits(fleet_root)

  after = (trigger_after or "").strip().lower()
  if after:
    if after == nm:
      return {"ok": False, "error": "a task cannot trigger itself"}
    if not any(h.name == after for h in habits):
      return {"ok": False, "error": f"upstream task not found: {after}"}
    from gemcode.habit_chains import would_create_habit_cycle

    if would_create_habit_cycle(habits, name=nm, trigger_after=after):
      return {"ok": False, "error": "trigger chain would create a cycle"}
    on = (trigger_on or "finished").strip().lower()
    if on not in ("finished", "failed", "any", "*"):
      return {"ok": False, "error": "trigger_on must be finished, failed, or any"}
    secs = None
    daily = None
    cron_expr = None
  else:
    secs = int(every_minutes) * 60 if every_minutes and every_minutes > 0 else None
    daily = (daily_at or "").strip() or None
    cron_expr = (cron or "").strip() or None
    if not secs and not daily and not cron_expr:
      return {"ok": False, "error": "must specify every_minutes, daily_at, cron, or trigger_after"}
    on = "finished"

  habits = [h for h in habits if h.name != nm]
  habits.append(
    Habit(
      name=nm,
      agent=agent.strip(),
      prompt=prompt.strip(),
      enabled=True,
      every_seconds=secs,
      daily_at=daily,
      cron=cron_expr,
      trigger_after=after or None,
      trigger_on=on,
      trigger_cooldown_s=float(trigger_cooldown_s or 0),
    )
  )
  save_habits(fleet_root, habits)
  scheduler = ensure_web_mesh_running(project_root)
  return {"ok": True, "name": nm, "agent": agent.strip(), "scheduler": scheduler}


def habit_runs_snapshot(
  project_root: Path,
  *,
  habit_name: str,
  limit: int = 50,
) -> dict[str, Any]:
  fleet_root = _habits_root(project_root)
  nm = (habit_name or "").strip().lower()
  if not nm:
    return {"ok": False, "error": "habit name is required"}

  habits = load_habits(fleet_root)
  habit = next((h for h in habits if h.name == nm), None)
  if habit is None:
    return {"ok": False, "error": f"habit not found: {nm}"}

  from gemcode.habit_runs import list_habit_runs

  runs = list_habit_runs(fleet_root, habit_name=nm, limit=limit)

  # Merge in-process mesh completions (same API pod) not yet persisted.
  try:
    from gemcode.config import GemCodeConfig
    from gemcode.agent_mesh import get_mesh

    mesh = get_mesh(GemCodeConfig(project_root=fleet_root))
    if mesh is not None:
      seen = {str(r.get("job_id") or "") for r in runs}
      with mesh._completed_lock:
        completed = list(mesh._completed)
      for job in reversed(completed):
        hm = job.meta.get("habit") if isinstance(job.meta, dict) else None
        if not isinstance(hm, dict) or str(hm.get("name") or "").strip().lower() != nm:
          continue
        if job.job_id in seen:
          continue
        runs.append(
          {
            "ts_ms": int(job.created_ms or 0),
            "habit_name": nm,
            "agent": str(hm.get("agent") or job.member_name or ""),
            "job_id": job.job_id,
            "status": str(job.status or "").strip().lower(),
            "report": str(job.result or "").strip()[:12_000],
            "error": str(job.error or "").strip()[:4000],
            "session_id": job.session_id,
            "live_mesh": True,
          }
        )
        seen.add(job.job_id)
      runs.sort(key=lambda r: int(r.get("ts_ms") or 0), reverse=True)
      runs = runs[:limit]
  except Exception:
    pass

  return {
    "ok": True,
    "habit_name": nm,
    "habit": {
      "name": habit.name,
      "agent": habit.agent,
      "prompt": habit.prompt,
      "enabled": habit.enabled,
      "schedule": _habit_schedule_label(habit.to_dict()),
      "run_count": habit.run_count,
      "last_run_ms": habit.last_run_ms,
    },
    "runs": runs,
    "total": len(runs),
    "fleet_root": str(fleet_root),
  }


def handle_habits_post(data: dict[str, Any], raw_path: str) -> tuple[int, dict[str, Any]]:
  root = _resolve_root(raw_path)
  if not root.is_dir():
    return 400, {"ok": False, "error": "project path is not a directory", "path": str(root)}

  action = str(data.get("action") or "").strip().lower()
  if action == "add":
    payload = habits_add_action(
      root,
      name=str(data.get("name") or ""),
      agent=str(data.get("agent") or ""),
      prompt=str(data.get("prompt") or ""),
      every_minutes=int(data.get("every_minutes") or 0),
      daily_at=str(data.get("daily_at") or ""),
      cron=str(data.get("cron") or ""),
      trigger_after=str(data.get("trigger_after") or ""),
      trigger_on=str(data.get("trigger_on") or "finished"),
      trigger_cooldown_s=float(data.get("trigger_cooldown_s") or 0),
    )
    return (200 if payload.get("ok") else 400), payload

  name = str(data.get("name") or "").strip()
  if action == "runs":
    try:
      limit = int(data.get("limit") or 50)
    except (TypeError, ValueError):
      limit = 50
    payload = habit_runs_snapshot(root, habit_name=name, limit=limit)
    return (200 if payload.get("ok") else 404), payload

  if action not in ("pause", "resume", "remove"):
    return 400, {"ok": False, "error": "action must be add, pause, resume, remove, or runs"}
  payload = habits_action(root, action=action, name=name)
  return (200 if payload.get("ok") else 400), payload


def handle_org_post(data: dict[str, Any], raw_path: str) -> tuple[int, dict[str, Any]]:
  root = _resolve_root(raw_path)
  if not root.is_dir():
    return 400, {"ok": False, "error": "project path is not a directory", "path": str(root)}

  action = str(data.get("action") or "hire").strip().lower()
  if action == "hire":
    payload = org_hire_action(
      root,
      name=str(data.get("name") or ""),
      title=str(data.get("title") or ""),
      kind=str(data.get("kind") or "subagent"),
      description=str(data.get("description") or ""),
      reports_to=str(data.get("reports_to") or "manager"),
    )
    return (200 if payload.get("ok") else 400), payload
  if action == "trigger":
    payload = org_trigger_action(
      root,
      member=str(data.get("member") or data.get("name") or ""),
      task=str(data.get("task") or data.get("prompt") or ""),
      session_id=str(data.get("session_id") or ""),
    )
    return (200 if payload.get("ok") else 400), payload
  return 400, {"ok": False, "error": "action must be hire or trigger"}


def handle_mesh_post(data: dict[str, Any], raw_path: str) -> tuple[int, dict[str, Any]]:
  root = _resolve_root(raw_path)
  if not root.is_dir():
    return 400, {"ok": False, "error": "project path is not a directory", "path": str(root)}
  action = str(data.get("action") or "").strip().lower()
  if action != "halt":
    return 400, {"ok": False, "error": "only action=halt is supported"}
  payload = mesh_halt_action(root, clear_habits=bool(data.get("clear_habits")))
  return (200 if payload.get("ok") else 400), payload


def handle_fleet_get(kind: str, raw_path: str | None) -> tuple[int, dict[str, Any]]:
  root = _resolve_root(raw_path)
  if not root.is_dir():
    return 400, {"ok": False, "error": "project path is not a directory", "path": str(root)}
  if kind == "org":
    return 200, org_snapshot(root)
  if kind == "habits":
    return 200, habits_snapshot(root)
  if kind == "mesh":
    return 200, mesh_snapshot(root)
  return 404, {"ok": False, "error": "unknown resource"}
