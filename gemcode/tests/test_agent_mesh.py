"""Tests for the in-process agent mesh."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path

import pytest

from gemcode.agent_mesh import (
  AgentMesh,
  _apply_mesh_worker_unattended_policy,
  ensure_mesh,
  get_mesh,
  reset_mesh,
)
from gemcode.config import GemCodeConfig
from gemcode.event_bus import BusMessage, get_bus, reset_bus


@pytest.fixture(autouse=True)
def _reset():
  reset_mesh()
  reset_bus()
  yield
  reset_mesh()
  reset_bus()


@pytest.mark.asyncio
async def test_lock_sqlite_session_same_key_reuses_lock(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  mesh = AgentMesh(cfg)
  db = tmp_path / ".gemcode" / "sessions.sqlite"
  a = await mesh._lock_sqlite_session(db_path=db, user_id="agent_x", session_id="sess1")
  b = await mesh._lock_sqlite_session(db_path=db, user_id="agent_x", session_id="sess1")
  assert a is b


@pytest.mark.asyncio
async def test_lock_sqlite_session_different_session_distinct_locks(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  mesh = AgentMesh(cfg)
  db = tmp_path / ".gemcode" / "sessions.sqlite"
  a = await mesh._lock_sqlite_session(db_path=db, user_id="agent_x", session_id="sess1")
  b = await mesh._lock_sqlite_session(db_path=db, user_id="agent_x", session_id="sess2")
  assert a is not b


def test_mesh_creation(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  mesh = ensure_mesh(cfg)
  assert mesh is not None
  assert mesh.max_concurrency == 3


def test_mesh_singleton(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  m1 = ensure_mesh(cfg)
  m2 = get_mesh(cfg)
  assert m1 is m2


def test_mesh_enqueue(tmp_path: Path) -> None:
  old_s = os.environ.get("PYTEST_GEMCODE_MESH_SCHEDULER")
  old_h = os.environ.get("GEMCODE_AGENT_HABITS")
  os.environ["PYTEST_GEMCODE_MESH_SCHEDULER"] = "0"
  os.environ["GEMCODE_AGENT_HABITS"] = "0"
  try:
    cfg = GemCodeConfig(project_root=tmp_path)
    mesh = AgentMesh(cfg, max_concurrency=2)
    job_id = mesh.enqueue(prompt="test task", priority=5, member_name="worker")
    assert job_id.startswith("mesh_")
    mesh.wait_for_pending_enqueues()
    assert mesh._queue.qsize() == 1
  finally:
    if old_s is None:
      os.environ.pop("PYTEST_GEMCODE_MESH_SCHEDULER", None)
    else:
      os.environ["PYTEST_GEMCODE_MESH_SCHEDULER"] = old_s
    if old_h is None:
      os.environ.pop("GEMCODE_AGENT_HABITS", None)
    else:
      os.environ["GEMCODE_AGENT_HABITS"] = old_h


def test_mesh_status(tmp_path: Path) -> None:
  old_s = os.environ.get("PYTEST_GEMCODE_MESH_SCHEDULER")
  old_h = os.environ.get("GEMCODE_AGENT_HABITS")
  os.environ["PYTEST_GEMCODE_MESH_SCHEDULER"] = "0"
  os.environ["GEMCODE_AGENT_HABITS"] = "0"
  try:
    cfg = GemCodeConfig(project_root=tmp_path)
    mesh = AgentMesh(cfg, max_concurrency=2)
    mesh.enqueue(prompt="task1", priority=1, member_name="a")
    mesh.enqueue(prompt="task2", priority=2, member_name="b")
    mesh.wait_for_pending_enqueues()
    status = mesh.status()
    assert status["queued_jobs"] == 2
    assert status["running_jobs"] == 0
    assert status["max_concurrency"] == 2
  finally:
    if old_s is None:
      os.environ.pop("PYTEST_GEMCODE_MESH_SCHEDULER", None)
    else:
      os.environ["PYTEST_GEMCODE_MESH_SCHEDULER"] = old_s
    if old_h is None:
      os.environ.pop("GEMCODE_AGENT_HABITS", None)
    else:
      os.environ["GEMCODE_AGENT_HABITS"] = old_h


def test_mesh_bus_integration(tmp_path: Path) -> None:
  """Verify that enqueue publishes a job.queued event to the bus."""
  cfg = GemCodeConfig(project_root=tmp_path)
  mesh = AgentMesh(cfg, max_concurrency=2)
  bus = get_bus()

  received: list[BusMessage] = []
  got = threading.Event()

  async def capture(msg: BusMessage) -> None:
    received.append(msg)
    got.set()

  async def run():
    bus.subscribe(topic="job.queued", callback=capture)
    mesh.enqueue(prompt="hello", priority=0, member_name="test")
    mesh.wait_for_pending_enqueues()

  asyncio.run(run())
  # Mesh loop processes publish asynchronously on a background thread.
  assert got.wait(timeout=3.0), "timed out waiting for job.queued on bus"
  time.sleep(0.01)
  assert len(received) == 1
  assert received[0].payload["member"] == "test"


def test_apply_mesh_worker_unattended_default_on(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.yes_to_all = False
  cfg.interactive_permission_ask = True
  old = os.environ.get("GEMCODE_MESH_WORKER_UNATTENDED")
  try:
    os.environ.pop("GEMCODE_MESH_WORKER_UNATTENDED", None)
    _apply_mesh_worker_unattended_policy(cfg)
  finally:
    if old is None:
      os.environ.pop("GEMCODE_MESH_WORKER_UNATTENDED", None)
    else:
      os.environ["GEMCODE_MESH_WORKER_UNATTENDED"] = old
  assert cfg.yes_to_all is True
  assert cfg.interactive_permission_ask is False


def test_apply_mesh_worker_unattended_off_inherits_manager(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.yes_to_all = False
  cfg.interactive_permission_ask = True
  old = os.environ.get("GEMCODE_MESH_WORKER_UNATTENDED")
  try:
    os.environ["GEMCODE_MESH_WORKER_UNATTENDED"] = "0"
    _apply_mesh_worker_unattended_policy(cfg)
  finally:
    if old is None:
      os.environ.pop("GEMCODE_MESH_WORKER_UNATTENDED", None)
    else:
      os.environ["GEMCODE_MESH_WORKER_UNATTENDED"] = old
  assert cfg.yes_to_all is False
  assert cfg.interactive_permission_ask is True


def test_mesh_halt_clears_pending_queue(tmp_path: Path) -> None:
  old_s = os.environ.get("PYTEST_GEMCODE_MESH_SCHEDULER")
  old_h = os.environ.get("GEMCODE_AGENT_HABITS")
  os.environ["PYTEST_GEMCODE_MESH_SCHEDULER"] = "0"
  os.environ["GEMCODE_AGENT_HABITS"] = "0"
  try:
    cfg = GemCodeConfig(project_root=tmp_path)
    mesh = AgentMesh(cfg, max_concurrency=2)
    mesh.enqueue(prompt="a", priority=1, member_name="x")
    mesh.enqueue(prompt="b", priority=1, member_name="y")
    mesh.wait_for_pending_enqueues()
    assert mesh._queue.qsize() == 2
    h = mesh.halt_jobs(clear_queue=True, cancel_running=False)
    assert h["cleared_queued"] == 2
    assert mesh._queue.qsize() == 0
  finally:
    if old_s is None:
      os.environ.pop("PYTEST_GEMCODE_MESH_SCHEDULER", None)
    else:
      os.environ["PYTEST_GEMCODE_MESH_SCHEDULER"] = old_s
    if old_h is None:
      os.environ.pop("GEMCODE_AGENT_HABITS", None)
    else:
      os.environ["GEMCODE_AGENT_HABITS"] = old_h


def test_mesh_priority_ordering(tmp_path: Path) -> None:
  """Higher priority jobs should be dequeued first."""
  old_s = os.environ.get("PYTEST_GEMCODE_MESH_SCHEDULER")
  old_h = os.environ.get("GEMCODE_AGENT_HABITS")
  os.environ["PYTEST_GEMCODE_MESH_SCHEDULER"] = "0"
  os.environ["GEMCODE_AGENT_HABITS"] = "0"
  try:
    cfg = GemCodeConfig(project_root=tmp_path)
    mesh = AgentMesh(cfg, max_concurrency=1)

    mesh.enqueue(prompt="low", priority=1, member_name="a")
    mesh.enqueue(prompt="high", priority=10, member_name="b")
    mesh.enqueue(prompt="mid", priority=5, member_name="c")
    mesh.wait_for_pending_enqueues()

    # Drain the queue manually to check ordering
    items = []
    while not mesh._queue.empty():
      neg_pri, seq, job = mesh._queue.get_nowait()
      items.append((job.prompt, job.priority))

    assert items[0] == ("high", 10)
    assert items[1] == ("mid", 5)
    assert items[2] == ("low", 1)
  finally:
    if old_s is None:
      os.environ.pop("PYTEST_GEMCODE_MESH_SCHEDULER", None)
    else:
      os.environ["PYTEST_GEMCODE_MESH_SCHEDULER"] = old_s
    if old_h is None:
      os.environ.pop("GEMCODE_AGENT_HABITS", None)
    else:
      os.environ["GEMCODE_AGENT_HABITS"] = old_h
