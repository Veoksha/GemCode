"""
In-process Agent Mesh — the orchestration backbone.

This module provides a lightweight, always-available agent coordination layer
that works WITHOUT the Kaira daemon. It manages:

1. Live agent instances (real ADK LlmAgents with their own Runners)
2. Job queue (asyncio priority queue for background work)
3. Event routing (via the in-memory EventBus)
4. Automatic result reporting (fleet reports + bus messages)

When the Kaira daemon IS running, the mesh bridges to it via IPC.
When it's NOT running, everything still works in-process.
"""

from __future__ import annotations

import asyncio
import copy
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig
from gemcode.event_bus import BusMessage, EventBus, get_bus
from gemcode.org import OrgMember, find_member, list_members, resolve_fleet_root


@dataclass
class AgentJob:
  """A unit of work for an agent."""
  job_id: str
  prompt: str
  priority: int = 0
  session_id: str = ""
  member_name: str = ""  # which org member should handle this
  meta: dict[str, Any] = field(default_factory=dict)
  status: str = "queued"  # queued, running, finished, failed
  result: str = ""
  error: str = ""
  created_ms: int = field(default_factory=lambda: int(time.time() * 1000))


class AgentMesh:
  """
  In-process agent orchestration mesh.

  Lifecycle:
  - Created once per GemCode session (attached to cfg)
  - Manages a priority queue of jobs
  - Runs jobs concurrently (up to max_concurrency)
  - Each job creates a fresh ADK Runner for the target agent
  - Results are published to the event bus and fleet reports
  """

  def __init__(self, cfg: GemCodeConfig, *, max_concurrency: int = 3) -> None:
    self.cfg = cfg
    self.max_concurrency = max(1, max_concurrency)
    self._bus = get_bus()
    self._queue: asyncio.PriorityQueue[tuple[int, int, AgentJob]] = asyncio.PriorityQueue()
    self._seq = 0
    self._sem = asyncio.Semaphore(self.max_concurrency)
    self._running: dict[str, asyncio.Task] = {}
    self._completed: list[AgentJob] = []
    self._scheduler_task: asyncio.Task | None = None
    self._stop = asyncio.Event()

    # Subscribe to org.assign messages on the bus
    self._bus.subscribe(
      topic="org.assign",
      to_addr="mesh",
      callback=self._handle_org_assign,
    )

    # Initialize self-triggering agents
    self._trigger_engine = None
    try:
      from gemcode.agent_triggers import TriggerEngine
      self._trigger_engine = TriggerEngine(cfg)
    except Exception:
      pass

    # Initialize delegation learning
    self._learner = None
    try:
      from gemcode.delegation_learning import DelegationLearner
      self._learner = DelegationLearner(cfg)
    except Exception:
      pass

    # Initialize habit scheduler (cron/interval/daily recurring tasks)
    self._habit_scheduler = None
    try:
      from gemcode.agent_habits import HabitScheduler
      self._habit_scheduler = HabitScheduler(cfg)
    except Exception:
      pass

    # Initialize self-healing loop (auto-verify + auto-fix after changes)
    self._self_healing = None
    try:
      from gemcode.self_healing import SelfHealingLoop
      self._self_healing = SelfHealingLoop(cfg)
    except Exception:
      pass

  @property
  def bus(self) -> EventBus:
    return self._bus

  def start(self) -> None:
    """Start the background scheduler loop and trigger engine."""
    if self._scheduler_task is None or self._scheduler_task.done():
      self._scheduler_task = asyncio.create_task(self._scheduler_loop())
    # Start self-triggering agents
    if self._trigger_engine is not None:
      self._trigger_engine.start()
    # Start habit scheduler (cron/interval recurring tasks)
    if self._habit_scheduler is not None:
      self._habit_scheduler.start()

  def stop(self) -> None:
    """Stop the scheduler and trigger engine."""
    self._stop.set()
    if self._scheduler_task and not self._scheduler_task.done():
      self._scheduler_task.cancel()
    if self._trigger_engine is not None:
      self._trigger_engine.stop()
    if self._habit_scheduler is not None:
      self._habit_scheduler.stop()

  def enqueue(
    self,
    *,
    prompt: str,
    priority: int = 0,
    session_id: str = "",
    member_name: str = "",
    meta: dict[str, Any] | None = None,
  ) -> str:
    """Enqueue a job and return its job_id."""
    job_id = f"mesh_{uuid.uuid4().hex[:10]}"
    self._seq += 1
    job = AgentJob(
      job_id=job_id,
      prompt=prompt,
      priority=priority,
      session_id=session_id or str(uuid.uuid4()),
      member_name=member_name,
      meta=meta or {},
    )
    # Higher priority = runs first (negate for min-heap)
    self._queue.put_nowait((-priority, self._seq, job))

    # Publish queued event
    self._bus.publish_sync(BusMessage(
      topic="job.queued",
      from_addr="mesh",
      to_addr="manager",
      payload={"job_id": job_id, "member": member_name, "priority": priority},
    ))

    # Auto-start scheduler if not running
    try:
      loop = asyncio.get_running_loop()
      if self._scheduler_task is None or self._scheduler_task.done():
        self._scheduler_task = loop.create_task(self._scheduler_loop())
    except RuntimeError:
      pass

    return job_id

  async def delegate_to_member(
    self,
    *,
    member: str | OrgMember,
    task: str,
    context: str = "",
    priority: int = 0,
    wait: bool = False,
  ) -> dict[str, Any]:
    """
    Delegate a task to an org member. This is the primary orchestration API.

    If wait=True, blocks until the job completes and returns the result.
    If wait=False, enqueues and returns immediately with the job_id.
    """
    fleet_root = resolve_fleet_root(self.cfg.project_root)

    if isinstance(member, str):
      m = find_member(fleet_root, member)
      if m is None:
        return {"ok": False, "error": f"unknown member: {member}"}
    else:
      m = member

    # Build the agent prompt with role context
    header = (
      f"You are {m.name} ({m.title}).\n"
      f"Role: {m.description or '(none)'}\n"
      f"Reports to: {m.reports_to or 'manager'}\n\n"
      "Complete the assigned task. Keep outputs concise and actionable.\n"
      "Return results as JSON when possible:\n"
      '{"status": "pass|fail|blocked", "summary": [...], "evidence": [...], "recommended_next_actions": [...]}\n\n'
    )

    # Auto-load the member's skill if they have one
    skill_preamble = ""
    if m.skill_name:
      try:
        from gemcode.skills import load_skill, expand_skill_text
        skill = load_skill(fleet_root, m.skill_name)
        if skill is not None:
          skill_preamble = expand_skill_text(skill, arguments="", session_id="") + "\n\n"
      except Exception:
        pass

    full_prompt = header + skill_preamble + "Task:\n" + task
    if context:
      full_prompt += "\n\nContext:\n" + context

    job_id = self.enqueue(
      prompt=full_prompt,
      priority=priority,
      session_id="",
      member_name=m.name,
      meta={
        "org": {
          "member": m.to_dict() if hasattr(m, "to_dict") else {},
          "task": task,
          "context": context,
        }
      },
    )

    if not wait:
      return {"ok": True, "job_id": job_id, "delegated_to": m.name, "async": True}

    # Wait for completion
    result = await self._wait_for_job(job_id, timeout=300.0)
    return result

  async def _wait_for_job(self, job_id: str, timeout: float = 300.0) -> dict[str, Any]:
    """Wait for a specific job to complete."""
    deadline = time.time() + timeout
    while time.time() < deadline:
      for job in self._completed:
        if job.job_id == job_id:
          if job.status == "finished":
            return {"ok": True, "job_id": job_id, "result": job.result}
          else:
            return {"ok": False, "job_id": job_id, "error": job.error}
      await asyncio.sleep(0.1)
    return {"ok": False, "job_id": job_id, "error": "timeout"}

  async def _handle_org_assign(self, msg: BusMessage) -> None:
    """Handle org.assign bus messages (A2A-style delegation)."""
    payload = msg.payload
    member = str(payload.get("member") or "").strip()
    task = str(payload.get("task") or "").strip()
    context = str(payload.get("context") or "").strip()
    if member and task:
      await self.delegate_to_member(member=member, task=task, context=context)

  async def _scheduler_loop(self) -> None:
    """Continuously dequeue and run jobs."""
    while not self._stop.is_set():
      try:
        await self._sem.acquire()
        try:
          _, _, job = await asyncio.wait_for(self._queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
          self._sem.release()
          continue

        task = asyncio.create_task(self._run_job(job))
        self._running[job.job_id] = task
        task.add_done_callback(lambda t, jid=job.job_id: self._on_job_done(jid))
      except asyncio.CancelledError:
        break
      except Exception:
        self._sem.release()
        continue

  def _on_job_done(self, job_id: str) -> None:
    """Callback when a job task completes."""
    self._running.pop(job_id, None)
    self._sem.release()

  async def _run_job(self, job: AgentJob) -> None:
    """Execute a single job using a fresh ADK Runner."""
    job.status = "running"
    start_ms = int(time.time() * 1000)

    # Publish started event
    await self._bus.publish(BusMessage(
      topic="job.started",
      from_addr=job.member_name or "mesh",
      to_addr="manager",
      payload={"job_id": job.job_id, "member": job.member_name},
    ))

    try:
      result_text = await self._execute_agent_turn(job)
      job.status = "finished"
      job.result = result_text
      duration_ms = int(time.time() * 1000) - start_ms

      # Publish completion
      await self._bus.publish(BusMessage(
        topic="job.report",
        from_addr=job.member_name or "mesh",
        to_addr="manager",
        payload={
          "job_id": job.job_id,
          "session_id": job.session_id,
          "status": "finished",
          "member": job.member_name,
          "report": result_text[:8000],
          "duration_ms": duration_ms,
        },
      ))

      # Also publish org.report if this was an org delegation
      org_meta = job.meta.get("org")
      if isinstance(org_meta, dict):
        await self._bus.publish(BusMessage(
          topic="org.report",
          from_addr=job.member_name or "mesh",
          to_addr="manager",
          payload={
            "member": org_meta.get("member", {}),
            "status": "finished",
            "task": org_meta.get("task", ""),
            "context": org_meta.get("context", ""),
            "job_id": job.job_id,
            "error": "",
            "result": {"report": result_text[:8000]},
          },
        ))

      # Record to delegation learning (with duration)
      try:
        from gemcode.delegation_learning import record_delegation
        record_delegation(
          self.cfg.project_root,
          member=job.member_name,
          task=org_meta.get("task", "") if isinstance(org_meta, dict) else job.prompt[:200],
          status="finished",
          result_summary=result_text[:500],
          duration_ms=duration_ms,
        )
      except Exception:
        pass

      # Persist to fleet reports
      try:
        from gemcode.fleet_reports import maybe_append_job_report
        fleet_root = resolve_fleet_root(self.cfg.project_root)
        maybe_append_job_report(fleet_root, {
          "job_id": job.job_id,
          "session_id": job.session_id,
          "status": "finished",
          "report": result_text[:8000],
        })
      except Exception:
        pass

    except Exception as e:
      job.status = "failed"
      job.error = f"{type(e).__name__}: {e}"

      await self._bus.publish(BusMessage(
        topic="job.report",
        from_addr=job.member_name or "mesh",
        to_addr="manager",
        payload={
          "job_id": job.job_id,
          "session_id": job.session_id,
          "status": "failed",
          "member": job.member_name,
          "error": job.error,
        },
      ))

      # Persist failure to fleet reports
      try:
        from gemcode.fleet_reports import maybe_append_job_report
        fleet_root = resolve_fleet_root(self.cfg.project_root)
        maybe_append_job_report(fleet_root, {
          "job_id": job.job_id,
          "session_id": job.session_id,
          "status": "failed",
          "report": job.error[:8000],
        })
      except Exception:
        pass

    finally:
      self._completed.append(job)
      # Keep completed list bounded
      if len(self._completed) > 200:
        self._completed = self._completed[-100:]

  async def _execute_agent_turn(self, job: AgentJob) -> str:
    """
    Run one agent turn as a FULL GemCode session.

    Each org member is a complete GemCode instance with:
    - Its own project root (workspace under .gemcode/agents/<id>-<slug>/)
    - Its own persistent SQLite session (survives restarts, accumulates history)
    - Its own memory (curated + embedding if enabled)
    - Its own skills (agent-local + project-level)
    - Full tool surface (filesystem, bash, grep, web, MCP, etc.)
    - Capability routing + model routing
    - Access to the mesh (can delegate to other agents)

    This means agents build up context over time — they remember past tasks,
    learn from their history, and maintain their own notes.
    """
    from gemcode.invoke import run_turn
    from gemcode.session_runtime import create_runner
    from gemcode.capability_routing import apply_capability_routing
    from gemcode.model_routing import pick_effective_model

    # Resolve the agent's workspace as their project root
    # This gives them their own .gemcode/ directory, sessions, memory, etc.
    agent_cfg = copy.deepcopy(self.cfg)
    fleet_root = resolve_fleet_root(self.cfg.project_root)

    if job.member_name:
      m = find_member(fleet_root, job.member_name)
      if m is not None and m.workspace_rel:
        agent_workspace = fleet_root / m.workspace_rel
        if agent_workspace.is_dir():
          # Agent gets its own project root = its own full GemCode environment
          agent_cfg.project_root = agent_workspace.resolve()
          # Ensure the agent has its own .gemcode directory
          (agent_cfg.project_root / ".gemcode").mkdir(parents=True, exist_ok=True)

    # Apply capability routing based on the job's prompt
    apply_capability_routing(agent_cfg, job.prompt, context="prompt")
    agent_cfg.model = pick_effective_model(agent_cfg, job.prompt)

    # Build mesh-specific extra tools (delegate, report)
    mesh_tools = self._build_mesh_tools_for_job(job)

    # Add a tool that lets the agent access the parent project files
    # (since their workspace is a subdirectory, they need to reach up)
    parent_tools = self._build_parent_access_tools(fleet_root)
    all_extra = (mesh_tools or []) + (parent_tools or [])

    # Create a FULL runner rooted at the agent's workspace
    # This gives them their own SQLite session DB, their own memory, etc.
    runner = create_runner(agent_cfg, extra_tools=all_extra or None)

    try:
      # Use a stable session ID per agent so they accumulate history
      # (not a random UUID — the same agent keeps the same session across jobs)
      stable_session_id = job.session_id
      if job.member_name:
        # Stable session = agent name hash (persists across jobs)
        import hashlib
        stable_session_id = f"agent_{hashlib.sha256(job.member_name.encode()).hexdigest()[:12]}"

      # Execute the turn with full power
      max_calls = min(int(self.cfg.max_llm_calls or 128), 128)
      events = await run_turn(
        runner,
        user_id=job.member_name or "mesh",
        session_id=stable_session_id,
        prompt=job.prompt,
        max_llm_calls=max_calls,
        cfg=agent_cfg,
        consume_fleet_reports=False,
      )

      # Extract text from events
      parts: list[str] = []
      for ev in events:
        try:
          if not ev.content or not ev.content.parts:
            continue
          if getattr(ev, "author", None) == "user":
            continue
          for part in ev.content.parts:
            t = getattr(part, "text", None)
            is_thought = getattr(part, "thought", None)
            if isinstance(t, str) and t.strip() and not is_thought:
              parts.append(t)
        except Exception:
          continue

      return "".join(parts).strip() or "(no output)"
    finally:
      # Clean up the runner
      try:
        await runner.close()
      except Exception:
        pass

  def _build_parent_access_tools(self, fleet_root: Path) -> list:
    """
    Tools that let an agent access the parent project's files.

    Since each agent runs in its own workspace (.gemcode/agents/<id>/),
    they need a way to read/write files in the actual project.
    """
    root = fleet_root

    def parent_read_file(path: str, start_line: int = 0, end_line: int = 0) -> dict:
      """Read a file from the parent project (not this agent's workspace)."""
      from pathlib import Path as P
      target = root / path
      if not target.is_file():
        return {"error": f"file not found: {path}"}
      try:
        text = target.read_text(encoding="utf-8", errors="replace")
        if start_line > 0 or end_line > 0:
          lines = text.splitlines(keepends=True)
          s = max(0, start_line - 1)
          e = end_line if end_line > 0 else len(lines)
          text = "".join(lines[s:e])
        return {"content": text[:100_000], "path": str(path)}
      except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    def parent_list_directory(path: str = ".") -> dict:
      """List files in the parent project directory."""
      from pathlib import Path as P
      target = root / path
      if not target.is_dir():
        return {"error": f"not a directory: {path}"}
      try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        items = []
        for e in entries[:200]:
          items.append({
            "name": e.name,
            "type": "dir" if e.is_dir() else "file",
          })
        return {"entries": items, "path": str(path)}
      except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    parent_read_file.__name__ = "parent_read_file"
    parent_list_directory.__name__ = "parent_list_directory"

    return [parent_read_file, parent_list_directory]

  def _build_mesh_tools_for_job(self, job: AgentJob) -> list:
    """Build tools that let a mesh agent delegate to other agents or report."""

    mesh = self

    async def mesh_delegate(member: str, task: str, context: str = "") -> dict:
      """Delegate a sub-task to another org member via the mesh."""
      result = await mesh.delegate_to_member(
        member=member,
        task=task,
        context=context,
        wait=True,
      )
      return result

    async def mesh_report(status: str, summary: str) -> dict:
      """Report your status back to the manager."""
      await mesh._bus.publish(BusMessage(
        topic="agent.report",
        from_addr=job.member_name or "worker",
        to_addr="manager",
        payload={
          "job_id": job.job_id,
          "status": status,
          "summary": summary,
          "member": job.member_name,
        },
      ))
      return {"ok": True, "reported": status}

    mesh_delegate.__name__ = "mesh_delegate"
    mesh_report.__name__ = "mesh_report"

    return [mesh_delegate, mesh_report]

  # ── Status / Introspection ──────────────────────────────────────────────

  def status(self) -> dict[str, Any]:
    """Get mesh status for debugging/display."""
    return {
      "running_jobs": len(self._running),
      "queued_jobs": self._queue.qsize(),
      "completed_jobs": len(self._completed),
      "max_concurrency": self.max_concurrency,
      "recent_completed": [
        {"job_id": j.job_id, "member": j.member_name, "status": j.status}
        for j in self._completed[-10:]
      ],
    }


# ── Global mesh singleton ──────────────────────────────────────────────────

_global_mesh: AgentMesh | None = None


def get_mesh(cfg: GemCodeConfig | None = None) -> AgentMesh | None:
  """Get or create the global agent mesh."""
  global _global_mesh
  if _global_mesh is None and cfg is not None:
    concurrency = int(os.environ.get("GEMCODE_MESH_CONCURRENCY", "3"))
    _global_mesh = AgentMesh(cfg, max_concurrency=concurrency)
  return _global_mesh


def ensure_mesh(cfg: GemCodeConfig) -> AgentMesh:
  """Ensure the global mesh exists and return it."""
  global _global_mesh
  if _global_mesh is None:
    concurrency = int(os.environ.get("GEMCODE_MESH_CONCURRENCY", "3"))
    _global_mesh = AgentMesh(cfg, max_concurrency=concurrency)
  return _global_mesh


def reset_mesh() -> None:
  """Reset the global mesh (for testing)."""
  global _global_mesh
  if _global_mesh is not None:
    _global_mesh.stop()
  _global_mesh = None
