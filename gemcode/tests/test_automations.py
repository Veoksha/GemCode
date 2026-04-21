from __future__ import annotations

import time
from pathlib import Path

from gemcode.automations import AutomationTrigger, automations_dir, is_due, load_automations, save_automation_state


def test_load_automations_interval(tmp_path: Path) -> None:
  d = automations_dir(tmp_path)
  d.mkdir(parents=True, exist_ok=True)
  (d / "a.json").write_text(
    '{ "name": "hb", "prompt": "hi", "triggers": [{"kind":"interval","every_seconds": 10}] }',
    encoding="utf-8",
  )
  autos = load_automations(tmp_path)
  assert len(autos) == 1
  assert autos[0].name == "hb"
  assert autos[0].triggers and autos[0].triggers[0].kind == "interval"


def test_is_due_interval() -> None:
  t = AutomationTrigger(kind="interval", every_seconds=10)
  now = 1000.0
  assert is_due(now_s=now, last_s=None, trig=t) is True
  assert is_due(now_s=now, last_s=995.0, trig=t) is False
  assert is_due(now_s=now, last_s=989.0, trig=t) is True


def test_is_due_cron_minute() -> None:
  # Force localtime minute/hour deterministically by using a known epoch.
  # We just assert that the function doesn't throw and behaves with last_s gate.
  t = AutomationTrigger(kind="cron", cron="* * * * *")
  now = time.time()
  assert is_due(now_s=now, last_s=None, trig=t) is True
  assert is_due(now_s=now, last_s=now, trig=t) is False


def test_save_state(tmp_path: Path) -> None:
  save_automation_state(tmp_path, {"a:interval:10": 123.0})
  p = automations_dir(tmp_path) / "state.json"
  assert p.is_file()

