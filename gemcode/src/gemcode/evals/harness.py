from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from gemcode.config import GemCodeConfig, load_cli_environment
from gemcode.invoke import run_turn
from gemcode.session_runtime import create_runner
from gemcode.tools_inspector import inspect_tools, smoke_tools


@dataclass
class EvalResult:
  name: str
  ok: bool
  score: float
  details: str = ""


def _discover_pytest_cwd(project_root: Path) -> tuple[Path, dict[str, str] | None] | None:
  """
  Return (cwd, env) for running pytest, or None if no tests tree found.

  ``env`` is ``None`` to inherit the process environment; otherwise a full env dict.

  Supports:
  - Monorepo layout: <root>/gemcode/tests → cwd gemcode, PYTHONPATH=src
  - Single-package layout: <root>/tests → cwd root
  """
  if (project_root / "tests").is_dir():
    return project_root, None
  gc = project_root / "gemcode"
  if (gc / "tests").is_dir():
    env = os.environ.copy()
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = "src" + (os.pathsep + prev if prev else "")
    return gc, env
  return None


def _events_to_text(events: list) -> str:
  parts: list[str] = []
  for event in events:
    try:
      if not event.content or not event.content.parts:
        continue
      if getattr(event, "author", None) == "user":
        continue
      for part in event.content.parts:
        t = getattr(part, "text", None)
        if t:
          parts.append(t)
    except Exception:
      continue
  return "".join(parts).strip()


async def _eval_golden_prompt(cfg: GemCodeConfig, prompt: str, *, name: str) -> EvalResult:
  runner = create_runner(cfg, extra_tools=None)
  try:
    events = await run_turn(
      runner,
      user_id="local",
      session_id=f"eval:{name}",
      prompt=prompt,
      max_llm_calls=min(int(getattr(cfg, "max_llm_calls", 256) or 256), 32),
      cfg=cfg,
    )
    text = _events_to_text(events)
  finally:
    await runner.close()

  if not text:
    return EvalResult(name=name, ok=False, score=0.0, details="empty_output")
  if "Traceback" in text or "SyntaxError" in text:
    return EvalResult(name=name, ok=False, score=0.0, details="looks_like_exception_text")
  return EvalResult(name=name, ok=True, score=1.0, details=text[:400])


def run_eval_suite(
  *,
  project_root: Path,
  include_llm: bool,
  model: str | None = None,
  session_cfg: GemCodeConfig | None = None,
  extra_tools: Iterable[Any] | None = None,
) -> dict[str, Any]:
  """
  Fixed evaluation harness (AutoResearch-style): deterministic gates + optional LLM golden prompts.

  When ``session_cfg`` is set (e.g. from the REPL), tool smoke uses that config so flags match the live session.
  """
  t0 = time.time()
  load_cli_environment()
  root = session_cfg.project_root if session_cfg is not None else project_root.resolve()
  if session_cfg is not None:
    cfg = replace(session_cfg, model=model, model_overridden=True) if model else session_cfg
  else:
    cfg = GemCodeConfig(project_root=project_root.resolve())
    if model:
      cfg.model = model
      cfg.model_overridden = True

  results: list[EvalResult] = []

  # Gate 1: tool declaration smoke (in-process; matches REPL config when session_cfg is passed)
  inspections = inspect_tools(cfg, extra_tools=extra_tools)
  failures = smoke_tools(inspections)
  ok_smoke = len(failures) == 0
  smoke_details = ""
  if failures:
    smoke_details = "\n".join(
      f"{f.name}: {f.declaration_error}" for f in failures[:40]
    )
  results.append(
    EvalResult(
      name="tools_smoke",
      ok=ok_smoke,
      score=1.0 if ok_smoke else 0.0,
      details=smoke_details[-1200:],
    )
  )

  # Gate 2: pytest if a tests/ tree exists under root or root/gemcode
  pytest_target = _discover_pytest_cwd(root)
  if pytest_target is not None:
    cwd, env = pytest_target
    p = subprocess.run(
      [sys.executable, "-m", "pytest", "-q"],
      cwd=str(cwd),
      env=env,
      capture_output=True,
      text=True,
    )
    out2 = (p.stdout or "") + (p.stderr or "")
    results.append(
      EvalResult(
        name="pytest",
        ok=(p.returncode == 0),
        score=1.0 if p.returncode == 0 else 0.0,
        details=out2[-1200:],
      )
    )

  if include_llm:
    goldens = [
      ("no_op_greeting", "hii"),
      ("explain_mode", "Explain what tools you have available, briefly."),
    ]
    async def _run():
      for n, p in goldens:
        results.append(await _eval_golden_prompt(cfg, p, name=n))
    asyncio.run(_run())

  ok_all = all(r.ok for r in results if r.name in ("tools_smoke", "pytest"))
  score = float(sum(r.score for r in results)) / max(1, len(results))
  elapsed = time.time() - t0

  return {
    "ok": bool(ok_all),
    "score": score,
    "elapsed_s": elapsed,
    "results": [r.__dict__ for r in results],
  }


def write_eval_record(project_root: Path, record: dict[str, Any]) -> Path:
  d = project_root / ".gemcode" / "evals"
  d.mkdir(parents=True, exist_ok=True)
  p = d / "last_eval.json"
  p.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
  return p

