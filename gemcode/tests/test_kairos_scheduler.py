from __future__ import annotations

import asyncio
from pathlib import Path

from gemcode.config import GemCodeConfig
from gemcode.kairos_daemon import KairosDaemon, KairosJob


def test_priority_ordering_drained_first_higher_priority(tmp_path: Path) -> None:
  started: list[str] = []

  async def job_runner(job: KairosJob) -> None:
    started.append(job.job_id)

  cfg = GemCodeConfig(project_root=tmp_path)
  daemon = KairosDaemon(cfg=cfg, concurrency=1, job_runner=job_runner)

  sid = "s"
  id_low = daemon.enqueue_prompt(prompt="low", priority=1, session_id=sid)
  id_high = daemon.enqueue_prompt(prompt="high", priority=3, session_id=sid)
  id_mid = daemon.enqueue_prompt(prompt="mid", priority=2, session_id=sid)

  asyncio.run(daemon.drain())

  assert started == [id_high, id_mid, id_low]


def test_enqueue_behavior_from_kairos_enqueue_prompt(tmp_path: Path) -> None:
  prompts: list[str] = []

  async def job_runner(job: KairosJob) -> None:
    prompts.append(job.prompt)

  cfg = GemCodeConfig(project_root=tmp_path)
  daemon = KairosDaemon(cfg=cfg, concurrency=1, job_runner=job_runner)

  sid = "s"
  current_job = KairosJob(
    job_id="current",
    prompt="irrelevant",
    priority=0,
    session_id=sid,
  )
  tools = daemon._build_extra_tools_for_job(current_job) or []
  enqueue_tool = next(
    t
    for t in tools
    if getattr(t, "__name__", "") == "kairos_enqueue_prompt"
  )

  out = enqueue_tool(prompt="from_model", priority=5)
  assert out.get("enqueued_job_id")

  asyncio.run(daemon.drain())
  assert prompts == ["from_model"]


def test_kairos_sleep_ms_calls_asyncio_sleep(tmp_path: Path, monkeypatch) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  daemon = KairosDaemon(cfg=cfg, concurrency=1, job_runner=lambda _job: asyncio.sleep(0))

  sid = "s"
  current_job = KairosJob(
    job_id="current",
    prompt="irrelevant",
    priority=0,
    session_id=sid,
  )
  tools = daemon._build_extra_tools_for_job(current_job) or []
  sleep_tool = next(t for t in tools if getattr(t, "__name__", "") == "kairos_sleep_ms")

  called: list[float] = []

  async def fake_sleep(seconds: float) -> None:
    called.append(seconds)

  import gemcode.kairos_daemon as kairos_mod

  monkeypatch.setattr(kairos_mod.asyncio, "sleep", fake_sleep)

  res = asyncio.run(sleep_tool(1500))
  assert res == {"slept_ms": 1500}
  assert called == [1.5]

