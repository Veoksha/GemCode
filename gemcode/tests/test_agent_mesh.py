"""Tests for the in-process agent mesh."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gemcode.agent_mesh import AgentMesh, ensure_mesh, get_mesh, reset_mesh
from gemcode.config import GemCodeConfig
from gemcode.event_bus import BusMessage, get_bus, reset_bus


@pytest.fixture(autouse=True)
def _reset():
  reset_mesh()
  reset_bus()
  yield
  reset_mesh()
  reset_bus()


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
  cfg = GemCodeConfig(project_root=tmp_path)
  mesh = AgentMesh(cfg, max_concurrency=2)
  job_id = mesh.enqueue(prompt="test task", priority=5, member_name="worker")
  assert job_id.startswith("mesh_")
  assert mesh._queue.qsize() == 1


def test_mesh_status(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  mesh = AgentMesh(cfg, max_concurrency=2)
  mesh.enqueue(prompt="task1", priority=1, member_name="a")
  mesh.enqueue(prompt="task2", priority=2, member_name="b")
  status = mesh.status()
  assert status["queued_jobs"] == 2
  assert status["running_jobs"] == 0
  assert status["max_concurrency"] == 2


def test_mesh_bus_integration(tmp_path: Path) -> None:
  """Verify that enqueue publishes a job.queued event to the bus."""
  cfg = GemCodeConfig(project_root=tmp_path)
  mesh = AgentMesh(cfg, max_concurrency=2)
  bus = get_bus()

  received: list[BusMessage] = []

  async def run():
    sub = bus.subscribe(topic="job.queued")
    mesh.enqueue(prompt="hello", priority=0, member_name="test")
    # Give the sync publish a moment to schedule
    await asyncio.sleep(0.1)
    msg = await sub.get(timeout=1.0)
    if msg:
      received.append(msg)

  asyncio.run(run())
  assert len(received) == 1
  assert received[0].payload["member"] == "test"


def test_mesh_priority_ordering(tmp_path: Path) -> None:
  """Higher priority jobs should be dequeued first."""
  cfg = GemCodeConfig(project_root=tmp_path)
  mesh = AgentMesh(cfg, max_concurrency=1)

  mesh.enqueue(prompt="low", priority=1, member_name="a")
  mesh.enqueue(prompt="high", priority=10, member_name="b")
  mesh.enqueue(prompt="mid", priority=5, member_name="c")

  # Drain the queue manually to check ordering
  items = []
  while not mesh._queue.empty():
    neg_pri, seq, job = mesh._queue.get_nowait()
    items.append((job.prompt, job.priority))

  assert items[0] == ("high", 10)
  assert items[1] == ("mid", 5)
  assert items[2] == ("low", 1)
