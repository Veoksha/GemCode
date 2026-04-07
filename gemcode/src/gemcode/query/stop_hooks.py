"""
Post-turn hooks (subset of legacy `query/stopHooks.ts`).

Runs after a user message finishes streaming through the agent. Optional:
- `GEMCODE_POST_TURN_HOOK` — path to an executable
- `.gemcode/hooks/post_turn` — if executable exists (and env not set)
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from gemcode.audit import append_audit
from gemcode.config import GemCodeConfig


def _is_executable(p: Path) -> bool:
  try:
    return p.is_file() and (p.stat().st_mode & stat.S_IXUSR)
  except OSError:
    return False


def run_post_turn_hooks(
    cfg: GemCodeConfig,
    *,
    session_id: str,
    user_id: str = "local",
    timeout_sec: float = 120.0,
) -> None:
  """Fire-and-forget-safe: catches errors, logs to audit."""
  hook = os.environ.get("GEMCODE_POST_TURN_HOOK")
  if not hook:
    candidate = cfg.project_root / ".gemcode" / "hooks" / "post_turn"
    if _is_executable(candidate):
      hook = str(candidate)
  if not hook:
    return
  path = Path(hook)
  if not path.is_file():
    append_audit(cfg.project_root, {"hook": "post_turn", "error": "missing_file", "path": hook})
    return
  env = os.environ.copy()
  env["GEMCODE_PROJECT_ROOT"] = str(cfg.project_root)
  env["GEMCODE_SESSION_ID"] = session_id
  env["GEMCODE_USER_ID"] = user_id
  try:
    subprocess.run(
        [str(path)],
        cwd=str(cfg.project_root),
        env=env,
        timeout=timeout_sec,
        capture_output=True,
        check=False,
    )
    append_audit(cfg.project_root, {"hook": "post_turn", "path": hook, "ok": True})
  except subprocess.TimeoutExpired:
    append_audit(cfg.project_root, {"hook": "post_turn", "path": hook, "error": "timeout"})
  except OSError as e:
    append_audit(cfg.project_root, {"hook": "post_turn", "path": hook, "error": str(e)})
