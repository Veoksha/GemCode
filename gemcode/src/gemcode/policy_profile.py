"""
Persistent per-repo policy profile.

Goal: make dynamic budgets self-tuning per repository without requiring manual
configuration. This stores lightweight rolling stats under `.gemcode/policy.json`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _path(root: Path) -> Path:
  d = root / ".gemcode"
  d.mkdir(parents=True, exist_ok=True)
  return d / "policy.json"


def _clamp(x: float, lo: float, hi: float) -> float:
  return lo if x < lo else hi if x > hi else x


def _ema(prev: float, x: float, *, alpha: float) -> float:
  return (alpha * x) + ((1.0 - alpha) * prev)


@dataclass(frozen=True)
class PolicyProfile:
  # Rolling averages in [0,1] where possible.
  failure_rate_ema: float = 0.0
  shell_rate_ema: float = 0.0
  write_rate_ema: float = 0.0
  files_touched_ema: float = 0.0  # scaled 0..1 (e.g. 0.5 ~ 10 files)
  updated_at: int = 0

  def to_dict(self) -> dict[str, Any]:
    return {
      "failure_rate_ema": self.failure_rate_ema,
      "shell_rate_ema": self.shell_rate_ema,
      "write_rate_ema": self.write_rate_ema,
      "files_touched_ema": self.files_touched_ema,
      "updated_at": self.updated_at,
      "version": 1,
    }

  @staticmethod
  def from_dict(d: dict[str, Any]) -> "PolicyProfile":
    try:
      return PolicyProfile(
        failure_rate_ema=float(d.get("failure_rate_ema", 0.0) or 0.0),
        shell_rate_ema=float(d.get("shell_rate_ema", 0.0) or 0.0),
        write_rate_ema=float(d.get("write_rate_ema", 0.0) or 0.0),
        files_touched_ema=float(d.get("files_touched_ema", 0.0) or 0.0),
        updated_at=int(d.get("updated_at", 0) or 0),
      )
    except Exception:
      return PolicyProfile()


def load_profile(project_root: Path) -> PolicyProfile:
  p = _path(project_root)
  if not p.exists():
    return PolicyProfile()
  try:
    raw = p.read_text(encoding="utf-8", errors="replace")
    d = json.loads(raw) if raw.strip() else {}
    if isinstance(d, dict):
      return PolicyProfile.from_dict(d)
  except Exception:
    return PolicyProfile()
  return PolicyProfile()


def save_profile(project_root: Path, profile: PolicyProfile) -> None:
  p = _path(project_root)
  p.write_text(
    json.dumps(profile.to_dict(), ensure_ascii=False, indent=2),
    encoding="utf-8",
    errors="replace",
  )


def update_profile(
  project_root: Path,
  *,
  files_touched: int,
  tool_calls: int,
  had_shell: bool,
  had_write: bool,
  had_failure: bool,
  alpha: float = 0.08,
) -> PolicyProfile:
  """
  Update profile with a single-turn observation.

  We scale files_touched into [0,1] via min(files/20, 1).
  """
  prof = load_profile(project_root)
  alpha = _clamp(alpha, 0.01, 0.3)
  ft_scaled = _clamp(float(files_touched) / 20.0, 0.0, 1.0)
  fail = 1.0 if had_failure else 0.0
  shell = 1.0 if had_shell else 0.0
  write = 1.0 if had_write else 0.0
  # tool_calls unused for now, but reserved for future calibration.
  _ = tool_calls
  updated = PolicyProfile(
    failure_rate_ema=_ema(prof.failure_rate_ema, fail, alpha=alpha),
    shell_rate_ema=_ema(prof.shell_rate_ema, shell, alpha=alpha),
    write_rate_ema=_ema(prof.write_rate_ema, write, alpha=alpha),
    files_touched_ema=_ema(prof.files_touched_ema, ft_scaled, alpha=alpha),
    updated_at=int(time.time()),
  )
  save_profile(project_root, updated)
  return updated


def calibrated_baseline_risk(profile: PolicyProfile) -> float:
  """
  Convert profile into a baseline risk prior for a repo.

  Repos with frequent failures, many writes, and lots of files touched tend to
  benefit from higher evidence budgets by default.
  """
  r = (
    0.55 * profile.failure_rate_ema
    + 0.20 * profile.write_rate_ema
    + 0.15 * profile.shell_rate_ema
    + 0.10 * profile.files_touched_ema
  )
  return _clamp(r, 0.0, 0.8)

