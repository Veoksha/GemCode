from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AutomationTrigger:
  kind: str  # interval|cron|daily
  every_seconds: int | None = None
  cron: str | None = None
  at_hhmm: str | None = None

  def key(self) -> str:
    if self.kind == "interval":
      return f"interval:{self.every_seconds}"
    if self.kind == "cron":
      return f"cron:{self.cron}"
    if self.kind == "daily":
      return f"daily:{self.at_hhmm}"
    return self.kind


@dataclass(frozen=True)
class Automation:
  name: str
  prompt: str
  priority: int = 0
  enabled: bool = True
  session_id: str | None = None
  triggers: tuple[AutomationTrigger, ...] = ()


def automations_dir(project_root: Path) -> Path:
  return project_root / ".gemcode" / "automations"


def automations_state_path(project_root: Path) -> Path:
  return automations_dir(project_root) / "state.json"


def load_automations(project_root: Path) -> list[Automation]:
  root = automations_dir(project_root)
  if not root.is_dir():
    return []
  out: list[Automation] = []
  for p in sorted(root.glob("*.json")):
    try:
      data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
      continue
    try:
      a = _parse_automation(data)
    except Exception:
      continue
    out.append(a)
  return out


def _parse_automation(data: dict[str, Any]) -> Automation:
  name = str(data.get("name") or "").strip()
  prompt = str(data.get("prompt") or "").strip()
  if not name or not prompt:
    raise ValueError("missing name/prompt")
  enabled = bool(data.get("enabled", True))
  priority = int(data.get("priority") or 0)
  session_id = (str(data.get("session_id")).strip() if data.get("session_id") else None)

  triggers_raw = data.get("triggers") or []
  if isinstance(triggers_raw, dict):
    triggers_raw = [triggers_raw]
  triggers: list[AutomationTrigger] = []
  for t in triggers_raw:
    if not isinstance(t, dict):
      continue
    kind = str(t.get("kind") or t.get("type") or "").strip().lower()
    if kind in ("interval", "every"):
      every = int(t.get("every_seconds") or t.get("every") or 0)
      if every <= 0:
        continue
      triggers.append(AutomationTrigger(kind="interval", every_seconds=every))
      continue
    if kind == "hourly":
      triggers.append(AutomationTrigger(kind="interval", every_seconds=3600))
      continue
    if kind in ("nightly", "daily"):
      at = str(t.get("at") or "02:00").strip()
      triggers.append(AutomationTrigger(kind="daily", at_hhmm=at))
      continue
    if kind == "cron":
      cron = str(t.get("cron") or "").strip()
      if not cron:
        continue
      triggers.append(AutomationTrigger(kind="cron", cron=cron))
      continue

  return Automation(
    name=name,
    prompt=prompt,
    priority=priority,
    enabled=enabled,
    session_id=session_id,
    triggers=tuple(triggers),
  )


def load_automation_state(project_root: Path) -> dict[str, float]:
  p = automations_state_path(project_root)
  if not p.is_file():
    return {}
  try:
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict):
      return {str(k): float(v) for k, v in data.items()}
  except Exception:
    pass
  return {}


def save_automation_state(project_root: Path, state: dict[str, float]) -> None:
  d = automations_dir(project_root)
  d.mkdir(parents=True, exist_ok=True)
  p = automations_state_path(project_root)
  try:
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
  except Exception:
    pass


def is_due(*, now_s: float, last_s: float | None, trig: AutomationTrigger) -> bool:
  if trig.kind == "interval":
    if not trig.every_seconds or trig.every_seconds <= 0:
      return False
    if last_s is None:
      return True
    return (now_s - last_s) >= float(trig.every_seconds)
  if trig.kind == "daily":
    at = trig.at_hhmm or "02:00"
    try:
      hh, mm = at.split(":", 1)
      h = int(hh)
      m = int(mm)
      if not (0 <= h <= 23 and 0 <= m <= 59):
        return False
    except Exception:
      return False
    # Compute today's fire time in local epoch seconds.
    lt = time.localtime(now_s)
    fire_today = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, lt.tm_wday, lt.tm_yday, lt.tm_isdst))
    # If we already passed today's fire time, next is tomorrow.
    fire_s = fire_today if now_s >= fire_today else fire_today - 86400.0
    # Due if we crossed the boundary since last_s.
    if last_s is None:
      return now_s >= fire_today
    return last_s < fire_today <= now_s
  if trig.kind == "cron":
    return _cron_due(now_s=now_s, last_s=last_s, cron=str(trig.cron or ""))
  return False


def _cron_due(*, now_s: float, last_s: float | None, cron: str) -> bool:
  # Minimal cron: "M H * * *" with *, */N, or integer for M/H.
  parts = (cron or "").split()
  if len(parts) != 5:
    return False
  m_s, h_s, dom, mon, dow = parts
  if dom != "*" or mon != "*" or dow != "*":
    return False

  def _match(field: str, val: int, *, min_v: int, max_v: int) -> bool:
    if field == "*":
      return True
    if field.startswith("*/"):
      try:
        step = int(field[2:])
        if step <= 0:
          return False
        return (val - min_v) % step == 0
      except Exception:
        return False
    try:
      x = int(field)
      return x == val and min_v <= x <= max_v
    except Exception:
      return False

  lt = time.localtime(now_s)
  if not (_match(m_s, lt.tm_min, min_v=0, max_v=59) and _match(h_s, lt.tm_hour, min_v=0, max_v=23)):
    return False
  # Trigger only once per matching minute.
  minute_start = now_s - float(lt.tm_sec)
  if last_s is None:
    return True
  return last_s < minute_start <= now_s

