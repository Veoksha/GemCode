from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

from gemcode.config import GemCodeConfig
from gemcode.evals.harness import run_eval_suite, write_eval_record


def _sh(cmd: list[str], *, cwd: Path) -> tuple[int, str]:
  p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
  out = (p.stdout or "") + (p.stderr or "")
  return int(p.returncode), out


def _git_head_sha(repo: Path) -> str | None:
  rc, out = _sh(["git", "rev-parse", "HEAD"], cwd=repo)
  if rc != 0:
    return None
  return (out or "").strip().splitlines()[-1] if (out or "").strip() else None


def _git_branch(repo: Path) -> str | None:
  rc, out = _sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
  if rc != 0:
    return None
  return (out or "").strip().splitlines()[-1] if (out or "").strip() else None


def init_autotune(*, project_root: Path, tag: str) -> dict[str, Any]:
  """
  AutoResearch-style setup:
  - create branch autotune/<tag> (if not exists)
  - create results ledger under .gemcode/evals/
  """
  repo = project_root
  if not (repo / ".git").exists():
    return {"error": "not_a_git_repo"}
  branch = f"autotune/{tag}"
  rc, out = _sh(["git", "rev-parse", "--verify", branch], cwd=repo)
  if rc == 0:
    return {"status": "exists", "branch": branch}
  rc2, out2 = _sh(["git", "checkout", "-b", branch], cwd=repo)
  if rc2 != 0:
    return {"error": "branch_create_failed", "output": out2[-1200:]}
  return {"status": "created", "branch": branch}


def run_autotune_eval(
  *,
  project_root: Path,
  include_llm: bool,
  model: str | None = None,
  session_cfg: GemCodeConfig | None = None,
  extra_tools: Iterable[Any] | None = None,
) -> dict[str, Any]:
  """
  Run eval suite and persist last result to .gemcode/evals/last_eval.json.
  """
  res = run_eval_suite(
    project_root=project_root,
    include_llm=include_llm,
    model=model,
    session_cfg=session_cfg,
    extra_tools=extra_tools,
  )
  meta = {
    "ts": time.time(),
    "git_sha": _git_head_sha(project_root),
    "git_branch": _git_branch(project_root),
  }
  p = write_eval_record(project_root, {**meta, **res})
  res["record_path"] = str(p)

  # Append ledger line (untracked; .gemcode/ is gitignored)
  try:
    ledger = project_root / ".gemcode" / "evals" / "autotune_ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    import json

    ledger.write_text("", encoding="utf-8") if not ledger.exists() else None
    with ledger.open("a", encoding="utf-8") as f:
      f.write(json.dumps({**meta, **res}, ensure_ascii=False) + "\n")
    res["ledger_path"] = str(ledger)
  except Exception:
    pass
  return res

