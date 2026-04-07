from __future__ import annotations

import asyncio
import copy
import sys
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from google.adk.runners import Runner

from gemcode.config import GemCodeConfig
from gemcode.capability_routing import apply_capability_routing
from gemcode.model_routing import pick_effective_model
from gemcode.invoke import run_turn
from gemcode.session_runtime import create_runner


def _events_to_text(events) -> str:
  parts: list[str] = []
  for event in events:
    if not getattr(event, "content", None) or not getattr(
      event.content, "parts", None
    ):
      continue
    for part in event.content.parts:
      if getattr(part, "text", None) and getattr(event, "author", None) != "user":
        parts.append(part.text)
  return "".join(parts)


@dataclass(frozen=True)
class KairaJob:
  job_id: str
  prompt: str
  priority: int
  session_id: str


class KairaDaemon:
  """Background proactive scheduler (stdin -> priority queue -> job runners)."""

  def __init__(
    self,
    *,
    cfg: GemCodeConfig,
    concurrency: int = 2,
    default_priority: int = 0,
    user_id: str = "local",
    job_runner: Callable[[KairaJob], Awaitable[None]] | None = None,
  ) -> None:
    self.cfg = cfg
    self.concurrency = max(1, int(concurrency))
    self.default_priority = int(default_priority)
    self.user_id = user_id

    # Queue items are (sort_key, seq, KairaJob).
    self._queue: asyncio.PriorityQueue[
      tuple[int, int, KairaJob]
    ] = asyncio.PriorityQueue()
    self._seq = 0
    self._sem = asyncio.Semaphore(self.concurrency)
    self._stop_event = asyncio.Event()

    self._job_runner = job_runner or self._default_job_runner

  def enqueue_prompt(
    self,
    *,
    prompt: str,
    priority: int | None = None,
    session_id: str,
  ) -> str:
    """Enqueue a new job into the priority queue and return job_id."""
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    pr = self.default_priority if priority is None else int(priority)
    self._seq += 1
    job = KairaJob(
      job_id=job_id,
      prompt=prompt,
      priority=pr,
      session_id=session_id,
    )
    # Higher priority should run first => use negative sort key.
    self._queue.put_nowait((-pr, self._seq, job))
    return job_id

  async def _default_job_runner(self, job: KairaJob) -> None:
    runner: Runner | None = None
    try:
      # Route model/capabilities based on this job's prompt, without mutating
      # the daemon's base config shared across jobs.
      job_cfg = copy.deepcopy(self.cfg)
      apply_capability_routing(job_cfg, job.prompt, context="prompt")
      job_cfg.model = pick_effective_model(job_cfg, job.prompt)

      # For the initial MVP, we inject Kaira tools via `_build_extra_tools_for_job()`;
      # this keeps scheduling logic independent from tool declarations.
      extra_tools = self._build_extra_tools_for_job(job)
      runner = create_runner(job_cfg, extra_tools=extra_tools or None)
      events = await run_turn(
        runner,
        user_id=self.user_id,
        session_id=job.session_id,
        prompt=job.prompt,
        max_llm_calls=job_cfg.max_llm_calls,
        cfg=job_cfg,
      )
      text = _events_to_text(events).strip()
      if text:
        print(f"\n[kaira {job.job_id}] {text}\n", flush=True)
      else:
        print(f"\n[kaira {job.job_id}] (no text output)\n", flush=True)
    finally:
      if runner is not None:
        await runner.close()

  def _build_extra_tools_for_job(self, job: KairaJob) -> list | None:
    """Inject per-job tools for the model to call."""

    async def kaira_sleep_ms(duration_ms: int) -> dict:
      """Pause this job for `duration_ms` (does not block other jobs)."""
      duration_ms = max(0, int(duration_ms))
      await asyncio.sleep(duration_ms / 1000.0)
      return {"slept_ms": duration_ms}

    def kaira_enqueue_prompt(
      prompt: str,
      priority: int = 0,
      session_id: str | None = None,
    ) -> dict:
      """Enqueue a new Kaira job from the model.

      If `session_id` is not provided, it defaults to the current job's
      session_id.
      """
      sid = job.session_id if session_id is None else str(session_id)
      enqueued_id = self.enqueue_prompt(
        prompt=prompt,
        priority=priority,
        session_id=sid,
      )
      return {"enqueued_job_id": enqueued_id}

    return [kaira_sleep_ms, kaira_enqueue_prompt]

  async def _run_job_with_semaphore(self, job: KairaJob) -> None:
    async with self._sem:
      await self._job_runner(job)

  async def _run_job_and_release(self, job: KairaJob) -> None:
    try:
      await self._job_runner(job)
    finally:
      self._sem.release()

  async def drain(self, *, max_jobs: int | None = None) -> int:
    """Process jobs already queued, useful for unit tests."""
    processed = 0
    while not self._queue.empty() and (max_jobs is None or processed < max_jobs):
      _, _, job = await self._queue.get()
      await self._run_job_with_semaphore(job)
      processed += 1
    return processed

  async def _stdin_loop(self, *, session_id: str) -> None:
    """Read stdin lines and enqueue each as a new job."""
    # Use a background thread so the asyncio loop stays responsive.
    prompt_prefix = "kaira> "
    while not self._stop_event.is_set():
      try:
        # Print prompt only in interactive terminals.
        if sys.stdin.isatty():
          print(prompt_prefix, end="", flush=True)
        line = await asyncio.to_thread(sys.stdin.readline)
      except Exception:
        break
      if not line:
        # EOF.
        break

      s = line.strip()
      if not s:
        continue
      if s.lower() in ("quit", "exit", "q"):
        self._stop_event.set()
        break

      # MVP: one line => one job at default priority.
      self.enqueue_prompt(
        prompt=s,
        priority=self.default_priority,
        session_id=session_id,
      )

  async def run_forever(self, *, session_id: str) -> None:
    """Start the scheduler and keep running until stdin EOF/quit."""

    scheduler_task = asyncio.create_task(self._scheduler_loop())
    stdin_task = asyncio.create_task(self._stdin_loop(session_id=session_id))

    # Wait for either scheduler to stop (shouldn't happen) or stdin loop to end.
    done, pending = await asyncio.wait(
      {scheduler_task, stdin_task},
      return_when=asyncio.FIRST_COMPLETED,
    )
    for p in pending:
      p.cancel()

  async def _scheduler_loop(self) -> None:
    """Continuously dequeue jobs by priority and run them."""
    while not self._stop_event.is_set():
      try:
        # Don't dequeue from the priority queue unless we can start work
        # immediately. This preserves priority ordering for "next starts".
        await self._sem.acquire()
        _, _, job = await self._queue.get()
      except asyncio.CancelledError:
        break
      asyncio.create_task(self._run_job_and_release(job))

