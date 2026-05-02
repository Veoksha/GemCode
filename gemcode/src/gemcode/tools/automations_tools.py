from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gemcode.automations import Automation, AutomationTrigger, automations_dir, load_automations
from gemcode.config import GemCodeConfig


def make_automations_tools(cfg: GemCodeConfig) -> list:
  """
  Tools for managing `.gemcode/automations/*.json`.

  These exist so the *normal* GemCode agent (not slash commands) can create and
  operate scheduled jobs as part of solving a task.
  """

  root = cfg.project_root

  def automations_list() -> dict[str, Any]:
    """List automation configs under `.gemcode/automations/*.json`."""
    autos = load_automations(root)
    out: list[dict[str, Any]] = []
    for a in autos:
      out.append(
        {
          "name": a.name,
          "enabled": bool(a.enabled),
          "priority": int(a.priority),
          "session_id": a.session_id,
          "triggers": [t.key() for t in (a.triggers or ())],
        }
      )
    return {
      "ok": True,
      "dir": str(automations_dir(root)),
      "count": len(out),
      "automations": out,
    }

  def automations_init(
    name: str,
    *,
    prompt: str = "Describe exactly what to do and what success looks like.",
    enabled: bool = True,
    priority: int = 0,
    trigger_kind: str = "nightly",
    at_hhmm: str = "02:00",
    every_seconds: int | None = None,
    cron: str | None = None,
    session_id: str | None = None,
    overwrite: bool = False,
  ) -> dict[str, Any]:
    """
    Create a new `.gemcode/automations/<name>.json` config (idempotent unless overwrite=true).

    trigger_kind:
      - nightly|daily (uses at_hhmm)
      - interval (uses every_seconds)
      - cron (uses cron)
    """
    import re

    nm = (name or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-_]{0,63}", nm):
      return {"ok": False, "error": "invalid name (use lowercase letters/numbers plus - or _; max 64 chars)"}

    a_dir = automations_dir(root)
    a_dir.mkdir(parents=True, exist_ok=True)
    p = a_dir / f"{nm}.json"
    if p.exists() and not overwrite:
      return {"ok": True, "path": str(p), "created": False}

    kind = (trigger_kind or "nightly").strip().lower()
    triggers: list[dict[str, Any]] = []
    if kind in ("nightly", "daily"):
      triggers = [{"kind": "nightly", "at": str(at_hhmm or "02:00")}]
    elif kind in ("interval", "every"):
      sec = int(every_seconds or 0)
      if sec <= 0:
        return {"ok": False, "error": "every_seconds must be > 0 for interval trigger"}
      triggers = [{"kind": "interval", "every_seconds": sec}]
    elif kind == "cron":
      c = str(cron or "").strip()
      if not c:
        return {"ok": False, "error": "cron must be non-empty for cron trigger"}
      triggers = [{"kind": "cron", "cron": c}]
    else:
      return {"ok": False, "error": "trigger_kind must be nightly|daily|interval|cron"}

    template = {
      "name": nm,
      "enabled": bool(enabled),
      "priority": int(priority),
      "prompt": str(prompt or "").strip() or "Describe exactly what to do and what success looks like.",
      "triggers": triggers,
    }
    if session_id:
      template["session_id"] = str(session_id)

    try:
      p.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as e:
      return {"ok": False, "error": f"write_failed: {type(e).__name__}: {e}"}

    return {"ok": True, "path": str(p), "created": True}

  async def automations_run(name: str) -> dict[str, Any]:
    """
    Enqueue an automation's prompt into the Kaira runtime now (best-effort).
    Requires a running manager runtime (see ``fleet_manager_ipc_path`` / ``manager_ipc.txt``).
    """
    target = (name or "").strip().lower()
    if not target:
      return {"ok": False, "error": "missing name"}
    cfgs = {a.name.lower(): a for a in load_automations(root)}
    a = cfgs.get(target)
    if a is None:
      return {"ok": False, "error": f"unknown automation: {target}"}
    if not a.enabled:
      return {"ok": False, "error": f"automation disabled: {a.name}"}

    try:
      from gemcode.kaira_client import KairaIpcClient
      from gemcode.kaira_ipc import fleet_manager_ipc_path_for_workspace

      sock = str(fleet_manager_ipc_path_for_workspace(root))
      client = await KairaIpcClient.connect(socket_path=sock)
      try:
        sid = (a.session_id or str(getattr(cfg, "_active_session_id", "") or ""))
        res = await client.request(action="enqueue", prompt=a.prompt, priority=a.priority, session_id=sid)
      finally:
        await client.close()
      if not res.get("ok"):
        return {"ok": False, "error": str(res.get("error") or "enqueue_failed")}
      return {"ok": True, "job_id": str(res.get("job_id") or ""), "automation": a.name}
    except Exception as e:
      return {"ok": False, "error": f"kaira_ipc_unavailable: {type(e).__name__}: {e}"}

  automations_list.__name__ = "automations_list"
  automations_init.__name__ = "automations_init"
  automations_run.__name__ = "automations_run"

  return [automations_list, automations_init, automations_run]

