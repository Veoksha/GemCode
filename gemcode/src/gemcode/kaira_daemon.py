from __future__ import annotations

import asyncio
import copy
import os
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from google.adk.runners import Runner

import json

from gemcode.config import GemCodeConfig
from gemcode.capability_routing import apply_capability_routing
from gemcode.model_routing import pick_effective_model
from gemcode.kaira_ipc import KairaIpcServer, default_ipc_socket_path, job_event
from gemcode.kaira_job_store import (
  KairaJobStore,
  JobRecord,
  mark_failed,
  mark_finished,
  mark_running,
  mark_cancelled,
  new_job_record,
)
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


def _extract_json_object(text: str) -> dict | None:
  raw = (text or "").strip()
  if not raw:
    return None
  try:
    obj = json.loads(raw)
    return obj if isinstance(obj, dict) else None
  except Exception:
    pass
  start = raw.find("{")
  end = raw.rfind("}")
  if start == -1 or end == -1 or end <= start:
    return None
  snippet = raw[start : end + 1]
  try:
    obj2 = json.loads(snippet)
    return obj2 if isinstance(obj2, dict) else None
  except Exception:
    return None


def _safe_tool_arg_summary(args: object) -> str:
  """One-line, low-risk summary for tool args (IPC/UI)."""
  try:
    if isinstance(args, dict):
      keys = list(args.keys())
      if len(keys) > 8:
        keys = keys[:8] + ["…"]
      return "{" + ", ".join(str(k) for k in keys) + "}"
  except Exception:
    pass
  return ""


def _fmt_tool_result(resp: object) -> str:
  """Best-effort one-line tool result summary for streaming."""
  try:
    d = resp if isinstance(resp, dict) else {}
    inner = d.get("result", d) if isinstance(d, dict) else d
    if not isinstance(inner, dict):
      inner = d if isinstance(d, dict) else {}
    err = inner.get("error") or (d.get("error") if isinstance(d, dict) else None)
    if err:
      return f"error: {str(err)[:160]}"
    exit_code = inner.get("exit_code")
    if exit_code is not None:
      return f"exit_code={exit_code}"
    if inner.get("ok") or (isinstance(d, dict) and d.get("ok")):
      return "ok"
  except Exception:
    pass
  return ""


def _should_stream_to_terminal() -> bool:
  """Stream live job output to the local terminal when interactive."""
  try:
    return bool(hasattr(sys.stdin, "isatty") and sys.stdin.isatty())
  except Exception:
    return False


def _stream_print(s: str) -> None:
  try:
    sys.stdout.write(s)
    sys.stdout.flush()
  except Exception:
    pass


async def _broadcast_text_delta(
  *,
  ipc: KairaIpcServer,
  job_id: str,
  session_id: str,
  emitted_text: str,
  new_text: str,
) -> str:
  if not new_text:
    return emitted_text
  delta = ""
  if new_text.startswith(emitted_text):
    delta = new_text[len(emitted_text) :]
  else:
    # Fallback: find common prefix.
    common = 0
    max_common = min(len(new_text), len(emitted_text))
    while common < max_common and new_text[common] == emitted_text[common]:
      common += 1
    delta = new_text[common:]
  if delta:
    await ipc.broadcast(
      job_event(
        "job_text_delta",
        job_id=job_id,
        session_id=session_id,
        delta=delta,
      )
    )
    return new_text
  return emitted_text


@dataclass(frozen=True)
class KairaJob:
  job_id: str
  prompt: str
  priority: int
  session_id: str
  meta: dict | None = None


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
    # If the runtime is started from inside an agent workspace (`.gemcode/agents/...`),
    # make it operate on the shared parent project root so it has access to the
    # full GemCode feature surface (org registry, MCP config, automations, etc.).
    try:
      from gemcode.org import resolve_fleet_root

      cfg.project_root = resolve_fleet_root(cfg.project_root)
    except Exception:
      pass

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
    self._ipc: KairaIpcServer | None = None
    self._store = KairaJobStore(project_root=cfg.project_root)
    self._job_records: dict[str, JobRecord] = {}
    self._cancelled: set[str] = set()
    self._running_tasks: dict[str, asyncio.Task] = {}
    self._default_session_id: str = ""
    self._manager_fix_requested: set[str] = set()

  def _manager_enabled(self) -> bool:
    return os.environ.get("GEMCODE_RUNTIME_MANAGER", "1").strip().lower() in (
      "1",
      "true",
      "yes",
      "on",
    )

  async def _handle_bus_message(self, ev: dict) -> None:
    """
    Minimal runtime "manager loop".

    - Translates selected bus messages into queued runs.
    - Keeps logic conservative to avoid runaway loops.
    """
    try:
      if not self._manager_enabled():
        return
      if not isinstance(ev, dict) or ev.get("type") != "event":
        return
      if str(ev.get("event") or "") != "bus_message":
        return
      topic = str(ev.get("topic") or "")
      payload = ev.get("payload")
      if not isinstance(payload, dict):
        return

      # A2A trigger: request an org delegation.
      if topic == "org.assign":
        member = str(payload.get("member") or "").strip()
        task = str(payload.get("task") or "").strip()
        context = str(payload.get("context") or "").strip()
        sid = str(payload.get("session_id") or "").strip() or self._default_session_id
        if not member or not task:
          return
        prompt = (
          "Delegate this task using org_delegate(member, task, context). "
          "After delegation, publish a bus_message topic=org.report to the manager with a concise result.\n\n"
          f"member={member}\n"
          f"task={task}\n"
          + (f"context={context}\n" if context else "")
        )
        self.enqueue_prompt(prompt=prompt, priority=5, session_id=sid)
        return

      # Failure recovery: if a job reports failure, enqueue one fix attempt.
      if topic == "job.report":
        status = str(payload.get("status") or "").strip().lower()
        job_id = str(payload.get("job_id") or "").strip()
        if status not in ("failed", "fail", "error"):
          return
        if not job_id or job_id in self._manager_fix_requested:
          return
        self._manager_fix_requested.add(job_id)
        sid = str(payload.get("session_id") or "").strip() or self._default_session_id
        report = str(payload.get("report") or "").strip()
        fix_prompt = (
          "You are GemCode runtime manager. The previous job failed.\n\n"
          "Task: diagnose and fix the failure with minimal changes.\n"
          "Rules:\n"
          "- Prefer the smallest safe fix.\n"
          "- If code changes are needed, implement and re-run the failing command(s).\n"
          "- End with a concise summary.\n\n"
          f"Failure job_id={job_id}\n\n"
          f"Failure report:\n{report}\n"
        )
        self.enqueue_prompt(prompt=fix_prompt, priority=6, session_id=sid)
        return
    except Exception:
      return

  async def _agent_heartbeat_loop(self, *, every_s: int) -> None:
    """
    Publish a lightweight heartbeat.

    - Always publishes to the local runtime bus (IPC subscribers).
    - Optionally publishes to a parent runtime socket when GEMCODE_PARENT_SOCKET is set.
    """
    every_s = max(1, int(every_s or 0))
    parent_sock = (os.environ.get("GEMCODE_PARENT_SOCKET") or "").strip()
    while not self._stop_event.is_set():
      try:
        payload = {
          "project_root": str(self.cfg.project_root),
          "model": getattr(self.cfg, "model", ""),
          "concurrency": int(self.concurrency),
        }
        ev = {
          "type": "event",
          "event": "bus_message",
          "topic": "agent.heartbeat",
          "to": "manager",
          "from_addr": "runtime",
          "payload": payload,
        }
        if self._ipc is not None:
          try:
            await self._ipc.broadcast(ev)
          except Exception:
            pass
        if parent_sock:
          try:
            from gemcode.kaira_client import KairaIpcClient
            c = await KairaIpcClient.connect(socket_path=parent_sock)
            try:
              await c.publish(topic="agent.heartbeat", payload=payload, to="manager", from_addr="child-runtime")
            finally:
              await c.close()
          except Exception:
            pass
      except Exception:
        pass
      await asyncio.sleep(float(every_s))

  def list_jobs(self, *, limit: int = 200) -> list[dict]:
    out: list[dict] = []
    for rec in self._store.list(limit=limit):
      out.append(rec.to_dict())
    return out

  def get_job(self, *, job_id: str) -> dict | None:
    rec = self._store.load(job_id)
    return rec.to_dict() if rec is not None else None

  def cancel_job(self, *, job_id: str) -> bool:
    self._cancelled.add(job_id)
    # Best-effort: cancel running asyncio task (does not necessarily abort ADK IO).
    t = self._running_tasks.get(job_id)
    if t is not None and not t.done():
      try:
        t.cancel()
      except Exception:
        pass
    rec = self._store.load(job_id)
    if rec is not None:
      try:
        self._store.upsert(mark_cancelled(rec))
      except Exception:
        pass
    if self._ipc is not None:
      try:
        asyncio.create_task(
          self._ipc.broadcast(job_event("job_cancelled", job_id=job_id, session_id=(rec.session_id if rec else "")))
        )
      except Exception:
        pass
    return True

  def set_concurrency(self, n: int) -> int:
    # Future: adjust live semaphore. For now we just return the current value.
    # We'll implement dynamic resizing once the rest of the control plane is stable.
    return int(self.concurrency)

  async def _automations_loop(
    self,
    *,
    session_id: str,
    heartbeat_every_s: int | None = None,
    heartbeat_prompt: str | None = None,
  ) -> None:
    """
    Run saved scheduled automations from `.gemcode/automations/*.json`.

    This is a simple local scheduler. It is intentionally conservative:
    - interval triggers can be as fast as seconds
    - cron/daily triggers are minute-level
    """
    import os

    from gemcode.automations import (
      is_due,
      load_automation_state,
      load_automations,
      save_automation_state,
    )

    state = load_automation_state(self.cfg.project_root)
    hb_last: float | None = None
    while not self._stop_event.is_set():
      try:
        if os.environ.get("GEMCODE_AUTOMATIONS", "0").strip().lower() not in ("1", "true", "yes", "on"):
          await asyncio.sleep(1.0)
          continue
        now_s = time.time()

        # Heartbeat (ephemeral; CLI-configured).
        if heartbeat_every_s and heartbeat_every_s > 0:
          if hb_last is None or (now_s - hb_last) >= float(heartbeat_every_s):
            hb_last = now_s
            p = (heartbeat_prompt or "Heartbeat: summarize running jobs and system status.").strip()
            if p:
              self.enqueue_prompt(prompt=p, priority=self.default_priority, session_id=session_id)

        autos = load_automations(self.cfg.project_root)
        changed = False
        for a in autos:
          if not a.enabled:
            continue
          for trig in a.triggers:
            key = f"{a.name}:{trig.key()}"
            last_s = state.get(key)
            if is_due(now_s=now_s, last_s=last_s, trig=trig):
              state[key] = now_s
              changed = True
              sid = a.session_id or session_id
              self.enqueue_prompt(prompt=a.prompt, priority=a.priority, session_id=sid)
        if changed:
          save_automation_state(self.cfg.project_root, state)
      except Exception:
        pass
      await asyncio.sleep(5.0)

  def enqueue_prompt(
    self,
    *,
    prompt: str,
    priority: int | None = None,
    session_id: str,
    meta: dict | None = None,
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
      meta=meta or None,
    )
    try:
      rec = new_job_record(job_id=job_id, session_id=session_id, priority=pr, prompt=prompt, meta=meta or None)
      self._job_records[job_id] = rec
      self._store.upsert(rec)
    except Exception:
      pass
    # Higher priority should run first => use negative sort key.
    self._queue.put_nowait((-pr, self._seq, job))
    if self._ipc is not None:
      try:
        # Fire-and-forget; IPC is best-effort.
        asyncio.create_task(
          self._ipc.broadcast(
            job_event(
              "job_queued",
              job_id=job_id,
              priority=pr,
              session_id=session_id,
            )
          )
        )
      except Exception:
        pass
    return job_id

  async def _default_job_runner(self, job: KairaJob) -> None:
    runner: Runner | None = None
    try:
      if self._ipc is not None:
        try:
          await self._ipc.broadcast(
            job_event(
              "job_started",
              job_id=job.job_id,
              priority=job.priority,
              session_id=job.session_id,
            )
          )
        except Exception:
          pass

      try:
        rec = self._job_records.get(job.job_id) or new_job_record(
          job_id=job.job_id,
          session_id=job.session_id,
          priority=job.priority,
          prompt=job.prompt,
          meta=(job.meta if isinstance(getattr(job, "meta", None), dict) else None),
        )
        self._job_records[job.job_id] = rec
        self._store.upsert(mark_running(rec))
      except Exception:
        pass

      # Route model/capabilities based on this job's prompt, without mutating
      # the daemon's base config shared across jobs.
      job_cfg = copy.deepcopy(self.cfg)
      apply_capability_routing(job_cfg, job.prompt, context="prompt")
      job_cfg.model = pick_effective_model(job_cfg, job.prompt)

      # For the initial MVP, we inject Kaira tools via `_build_extra_tools_for_job()`;
      # this keeps scheduling logic independent from tool declarations.
      extra_tools = self._build_extra_tools_for_job(job)
      runner = create_runner(job_cfg, extra_tools=extra_tools or None)
      events = await self._run_job_streaming_with_hitl(job=job, runner=runner, max_llm_calls=job_cfg.max_llm_calls)

      text = _events_to_text(events).strip()
      if text:
        print(f"\n[kaira {job.job_id}] {text}\n", flush=True)
      else:
        print(f"\n[kaira {job.job_id}] (no text output)\n", flush=True)
      if self._ipc is not None:
        try:
          await self._ipc.broadcast(
            job_event("job_finished", job_id=job.job_id, session_id=job.session_id)
          )
        except Exception:
          pass
      try:
        rec2 = self._job_records.get(job.job_id)
        if rec2 is not None:
          rec2.last_text = text or ""
          self._store.upsert(mark_finished(rec2))
          if self._ipc is not None:
            try:
              # Structured report (preferred): if worker returned JSON, include it.
              report_obj = _extract_json_object(text or "")
              await self._ipc.broadcast(
                job_event(
                  "job_report",
                  job_id=job.job_id,
                  session_id=job.session_id,
                  status="finished",
                  report=(text or "")[:8000],
                  report_json=report_obj,
                )
              )
              # Also emit to the general bus so other GemCode clients can subscribe
              # without parsing job_* events.
              await self._ipc.broadcast(
                {
                  "type": "event",
                  "event": "bus_message",
                  "topic": "job.report",
                  "to": "manager",
                  "from_addr": "runtime",
                  "payload": {
                    "job_id": job.job_id,
                    "session_id": job.session_id,
                    "status": "finished",
                    "report_json": report_obj,
                    "report": (text or "")[:8000],
                  },
                }
              )
              # Process internally too (so the runtime can self-manage without
              # requiring an external client to re-publish).
              await self._handle_bus_message(
                {
                  "type": "event",
                  "event": "bus_message",
                  "topic": "job.report",
                  "to": "manager",
                  "from_addr": "runtime",
                  "payload": {
                    "job_id": job.job_id,
                    "session_id": job.session_id,
                    "status": "finished",
                    "report_json": report_obj,
                    "report": (text or "")[:8000],
                  },
                }
              )
              try:
                meta_inbox = getattr(rec2, "meta", None) if isinstance(rec2, JobRecord) else None
                org_inbox = meta_inbox.get("org") if isinstance(meta_inbox, dict) else None
                if not isinstance(org_inbox, dict):
                  from gemcode.fleet_reports import maybe_append_job_report

                  maybe_append_job_report(
                    self.cfg.project_root,
                    {
                      "job_id": job.job_id,
                      "session_id": job.session_id,
                      "status": "finished",
                      "report_json": report_obj,
                      "report": (text or "")[:8000],
                    },
                  )
              except Exception:
                pass

              # If this job was enqueued as an org delegation, automatically publish
              # an org.report back to the manager (and any notify_chain).
              try:
                meta = getattr(rec2, "meta", None) if isinstance(rec2, JobRecord) else None
                org = meta.get("org") if isinstance(meta, dict) else None
                if isinstance(org, dict):
                  member = org.get("member")
                  task = str(org.get("task") or "").strip()
                  context = str(org.get("context") or "").strip()
                  notify_chain = org.get("notify_chain")
                  if not isinstance(notify_chain, list):
                    notify_chain = ["manager"]
                  payload = {
                    "member": member if isinstance(member, dict) else {},
                    "capabilities": org.get("capabilities") if isinstance(org.get("capabilities"), dict) else {},
                    "status": "finished",
                    "task": task,
                    "context": context,
                    "job_id": job.job_id,
                    "error": "",
                    "result": {
                      "report_json": report_obj,
                      "report": (text or "")[:8000],
                    },
                    "notify_chain": notify_chain,
                  }
                  for to_addr in notify_chain:
                    await self._ipc.broadcast(
                      {
                        "type": "event",
                        "event": "bus_message",
                        "topic": "org.report",
                        "to": str(to_addr or "manager"),
                        "from_addr": "runtime",
                        "payload": payload,
                      }
                    )
                  try:
                    from gemcode.fleet_reports import maybe_append_org_report

                    maybe_append_org_report(self.cfg.project_root, payload)
                  except Exception:
                    pass
              except Exception:
                pass
            except Exception:
              pass
      except Exception:
        pass
    except Exception as e:
      if self._ipc is not None:
        try:
          await self._ipc.broadcast(
            job_event(
              "job_failed",
              job_id=job.job_id,
              session_id=job.session_id,
              error=f"{type(e).__name__}: {e}",
            )
          )
        except Exception:
          pass
        try:
          report_obj = _extract_json_object(str(e))
          await self._ipc.broadcast(
            job_event(
              "job_report",
              job_id=job.job_id,
              session_id=job.session_id,
              status="failed",
              report=(f"{type(e).__name__}: {e}")[:8000],
              report_json=report_obj,
            )
          )
          await self._ipc.broadcast(
            {
              "type": "event",
              "event": "bus_message",
              "topic": "job.report",
              "to": "manager",
              "from_addr": "runtime",
              "payload": {
                "job_id": job.job_id,
                "session_id": job.session_id,
                "status": "failed",
                "report_json": report_obj,
                "report": (f"{type(e).__name__}: {e}")[:8000],
              },
            }
          )
          await self._handle_bus_message(
            {
              "type": "event",
              "event": "bus_message",
              "topic": "job.report",
              "to": "manager",
              "from_addr": "runtime",
              "payload": {
                "job_id": job.job_id,
                "session_id": job.session_id,
                "status": "failed",
                "report_json": report_obj,
                "report": (f"{type(e).__name__}: {e}")[:8000],
              },
            }
          )
          try:
            recf0 = self._job_records.get(job.job_id)
            m0 = getattr(recf0, "meta", None) if recf0 is not None else None
            org0 = m0.get("org") if isinstance(m0, dict) else None
            if not isinstance(org0, dict):
              from gemcode.fleet_reports import maybe_append_job_report

              maybe_append_job_report(
                self.cfg.project_root,
                {
                  "job_id": job.job_id,
                  "session_id": job.session_id,
                  "status": "failed",
                  "report_json": report_obj,
                  "report": (f"{type(e).__name__}: {e}")[:8000],
                },
              )
          except Exception:
            pass
        except Exception:
          pass
        # Also emit org.report failures for org-delegated jobs.
        try:
          recf = self._job_records.get(job.job_id)
          meta = getattr(recf, "meta", None) if recf is not None else None
          org = meta.get("org") if isinstance(meta, dict) else None
          if isinstance(org, dict):
            member = org.get("member")
            task = str(org.get("task") or "").strip()
            context = str(org.get("context") or "").strip()
            notify_chain = org.get("notify_chain")
            if not isinstance(notify_chain, list):
              notify_chain = ["manager"]
            payload = {
              "member": member if isinstance(member, dict) else {},
              "capabilities": org.get("capabilities") if isinstance(org.get("capabilities"), dict) else {},
              "status": "failed",
              "task": task,
              "context": context,
              "job_id": job.job_id,
              "error": f"{type(e).__name__}: {e}",
              "result": None,
              "notify_chain": notify_chain,
            }
            for to_addr in notify_chain:
              await self._ipc.broadcast(
                {
                  "type": "event",
                  "event": "bus_message",
                  "topic": "org.report",
                  "to": str(to_addr or "manager"),
                  "from_addr": "runtime",
                  "payload": payload,
                }
              )
            try:
              from gemcode.fleet_reports import maybe_append_org_report

              maybe_append_org_report(self.cfg.project_root, payload)
            except Exception:
              pass
        except Exception:
          pass
      try:
        rec3 = self._job_records.get(job.job_id)
        if rec3 is not None:
          self._store.upsert(mark_failed(rec3, f"{type(e).__name__}: {e}"))
      except Exception:
        pass
      raise
    finally:
      if runner is not None:
        await runner.close()

  async def _run_job_streaming_with_hitl(
    self,
    *,
    job: KairaJob,
    runner: Runner,
    max_llm_calls: int | None,
  ) -> list:
    """Run one job and stream events; bridge tool confirmations via IPC when possible."""
    from google.adk.agents.run_config import RunConfig
    from google.genai import types

    REQUEST_CONFIRMATION_FC = "adk_request_confirmation"

    run_config = (
      RunConfig(max_llm_calls=max_llm_calls) if max_llm_calls is not None else None
    )

    def _get_confirmation_requests(events: list) -> list:
      out: list = []
      for ev in events:
        try:
          for fc in ev.get_function_calls() or []:
            if getattr(fc, "name", None) == REQUEST_CONFIRMATION_FC:
              out.append(fc)
        except Exception:
          continue
      return out

    def _extract_hint_and_tool(fc) -> tuple[str, str]:
      tool_name = "unknown_tool"
      hint = ""
      try:
        args = getattr(fc, "args", None) or {}
        orig = args.get("originalFunctionCall") or {}
        tool_name = orig.get("name") or tool_name
        tc = args.get("toolConfirmation") or {}
        hint = tc.get("hint") or ""
      except Exception:
        pass
      return tool_name, hint

    async def _stream_one_message(*, current_message: types.Content) -> tuple[list, str]:
      emitted_text = ""
      events: list = []
      stream_live = _should_stream_to_terminal()
      if stream_live:
        _stream_print(f"\n[kaira {job.job_id}] started\n")
      async for ev in runner.run_async(
        user_id=self.user_id,
        session_id=job.session_id,
        new_message=current_message,
        **({"run_config": run_config} if run_config is not None else {}),
      ):
        events.append(ev)
        # Live terminal streaming (independent of IPC).
        if stream_live:
          try:
            from gemcode.web.sse_adapter import extract_text_from_event

            txt_live = extract_text_from_event(ev)
            if txt_live:
              if txt_live.startswith(emitted_text):
                delta_live = txt_live[len(emitted_text) :]
              else:
                # Fallback: find common prefix.
                common = 0
                max_common = min(len(txt_live), len(emitted_text))
                while common < max_common and txt_live[common] == emitted_text[common]:
                  common += 1
                delta_live = txt_live[common:]
              if delta_live:
                _stream_print(delta_live)
                emitted_text = txt_live
          except Exception:
            pass

        if self._ipc is None:
          continue

        # Tool calls
        try:
          for fc in ev.get_function_calls() or []:
            name = getattr(fc, "name", "") or ""
            if not name or name == REQUEST_CONFIRMATION_FC:
              continue
            args = getattr(fc, "args", None) or {}
            await self._ipc.broadcast(
              job_event(
                "job_tool_call",
                job_id=job.job_id,
                session_id=job.session_id,
                tool=name,
                args_summary=_safe_tool_arg_summary(args),
              )
            )
        except Exception:
          pass

        # Tool results
        try:
          frs: list = []
          try:
            frs = ev.get_function_responses() or []
          except Exception:
            frs = []
          if not frs and getattr(ev, "content", None) and getattr(ev.content, "parts", None):
            for part in ev.content.parts:
              fr = getattr(part, "function_response", None)
              if fr is not None:
                frs.append(fr)
          for fr in frs:
            nm = getattr(fr, "name", "") or ""
            if not nm or nm == REQUEST_CONFIRMATION_FC:
              continue
            resp = getattr(fr, "response", {}) or {}
            await self._ipc.broadcast(
              job_event(
                "job_tool_result",
                job_id=job.job_id,
                session_id=job.session_id,
                tool=nm,
                summary=_fmt_tool_result(resp),
              )
            )
        except Exception:
          pass

        # Text deltas (IPC subscribers)
        try:
          from gemcode.web.sse_adapter import extract_text_from_event

          txt = extract_text_from_event(ev)
          if txt:
            emitted_text = await _broadcast_text_delta(
              ipc=self._ipc,
              job_id=job.job_id,
              session_id=job.session_id,
              emitted_text=emitted_text,
              new_text=txt,
            )
        except Exception:
          pass

      return events, emitted_text

    collected: list = []
    current_message = types.Content(role="user", parts=[types.Part(text=job.prompt)])
    while True:
      events, _emitted = await _stream_one_message(current_message=current_message)
      collected.extend(events)

      confirmation_fcs = _get_confirmation_requests(events)
      if not confirmation_fcs:
        break

      parts: list[types.Part] = []
      for fc in confirmation_fcs:
        tool_name, hint = _extract_hint_and_tool(fc)
        auto_ok = bool(
          getattr(self.cfg, "yes_to_all", False)
          or getattr(self.cfg, "super_mode", False)
        )
        ok = bool(auto_ok)
        if not ok and self._ipc is not None:
          try:
            ok = await self._ipc.request_confirmation(
              job_id=job.job_id,
              session_id=job.session_id,
              tool=tool_name,
              hint=hint,
              timeout_s=300.0,
            )
          except Exception:
            ok = False
        parts.append(
          types.Part(
            function_response=types.FunctionResponse(
              name=REQUEST_CONFIRMATION_FC,
              id=getattr(fc, "id", None),
              response={"confirmed": bool(ok)},
            )
          )
        )
      current_message = types.Content(role="user", parts=parts)

    return collected

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
      t = asyncio.current_task()
      if t is not None:
        self._running_tasks[job.job_id] = t
      await self._job_runner(job)
    finally:
      self._running_tasks.pop(job.job_id, None)
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

  async def run_forever(self, *, session_id: str, enable_stdin: bool = True) -> None:
    """Start the scheduler and keep running until stopped.

    When enable_stdin=False, Kaira runs headless (IPC-only) and does not read
    from stdin. This mode is used when embedding Kaira inside the GemCode TUI.
    """

    self._default_session_id = session_id

    # Start IPC server for two-way control + event streaming.
    try:
      from pathlib import Path

      sock_env = os.environ.get("GEMCODE_KAIRA_SOCKET")
      sock_path = (
        Path(sock_env).expanduser()
        if sock_env and str(sock_env).strip()
        else default_ipc_socket_path(self.cfg.project_root)
      )
      async def _on_bus(ev: dict) -> None:
        await self._handle_bus_message(ev)
      self._ipc = KairaIpcServer(
        socket_path=sock_path,
        enqueue_fn=self.enqueue_prompt,
        publish_hook=_on_bus,
        list_jobs_fn=lambda limit=200: self.list_jobs(limit=limit),
        get_job_fn=lambda job_id: self.get_job(job_id=job_id),
        cancel_job_fn=lambda job_id: self.cancel_job(job_id=job_id),
        set_concurrency_fn=lambda n: self.set_concurrency(n),
      )
      await self._ipc.start()
      print(f"[kaira] ipc_socket={sock_path}", file=sys.stderr, flush=True)
    except Exception as e:
      self._ipc = None
      print(f"[kaira] ipc disabled: {e}", file=sys.stderr, flush=True)

    import os as _os

    scheduler_task = asyncio.create_task(self._scheduler_loop())
    automations_task = asyncio.create_task(
      self._automations_loop(
        session_id=session_id,
        heartbeat_every_s=int(_os.environ.get("GEMCODE_KAIRA_HEARTBEAT_EVERY_S", "0") or "0") or None,
        heartbeat_prompt=_os.environ.get("GEMCODE_KAIRA_HEARTBEAT_PROMPT", None),
      )
    )
    hb_task = None
    try:
      every = int(_os.environ.get("GEMCODE_AGENT_HEARTBEAT_EVERY_S", "0") or "0")
      if every > 0:
        hb_task = asyncio.create_task(self._agent_heartbeat_loop(every_s=every))
    except Exception:
      hb_task = None
    stdin_task = None
    if enable_stdin:
      stdin_task = asyncio.create_task(self._stdin_loop(session_id=session_id))

    # Wait for either scheduler to stop (shouldn't happen) or stdin loop to end.
    wait_set = {scheduler_task, automations_task}
    if hb_task is not None:
      wait_set.add(hb_task)
    if stdin_task is not None:
      wait_set.add(stdin_task)
    done, pending = await asyncio.wait(
      wait_set,
      return_when=asyncio.FIRST_COMPLETED,
    )
    for p in pending:
      p.cancel()

    if self._ipc is not None:
      try:
        await self._ipc.close()
      except Exception:
        pass

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
      if job.job_id in self._cancelled:
        # Skip cancelled jobs.
        self._sem.release()
        continue
      asyncio.create_task(self._run_job_and_release(job))

