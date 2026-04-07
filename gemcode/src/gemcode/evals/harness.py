from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from gemcode.config import GemCodeConfig, load_cli_environment
from gemcode.invoke import run_turn
from gemcode.session_runtime import create_runner


@dataclass
class EvalResult:
  name: str
  ok: bool
  score: float
  details: str = ""


def _run_cmd(cmd: str, *, cwd: Path) -> tuple[int, str]:
  import subprocess
  p = subprocess.run(cmd, cwd=str(cwd), shell=True, capture_output=True, text=True)
  out = (p.stdout or "") + (p.stderr or "")
  return int(p.returncode), out


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
) -> dict[str, Any]:
  """
  Fixed evaluation harness (AutoResearch-style): deterministic gates + optional LLM golden prompts.
  """
  t0 = time.time()
  load_cli_environment()
  cfg = GemCodeConfig(project_root=project_root)
  if model:
    cfg.model = model
    cfg.model_overridden = True

  results: list[EvalResult] = []

  # Gate 1: tool schema smoke
  rc, out = _run_cmd("PYTHONPATH=src python3 -m gemcode tools smoke", cwd=project_root / "gemcode")
  results.append(EvalResult(name="tools_smoke", ok=(rc == 0), score=1.0 if rc == 0 else 0.0, details=out[-800:]))

  # Gate 2: pytest if present
  tests_dir = project_root / "gemcode" / "tests"
  if tests_dir.is_dir():
    rc2, out2 = _run_cmd("PYTHONPATH=src python3 -m pytest -q", cwd=project_root / "gemcode")
    results.append(EvalResult(name="pytest", ok=(rc2 == 0), score=1.0 if rc2 == 0 else 0.0, details=out2[-1200:]))

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

