"""Tests for the in-memory event bus."""

from __future__ import annotations

import asyncio

import pytest

from gemcode.event_bus import BusMessage, EventBus, get_bus, reset_bus


@pytest.fixture(autouse=True)
def _reset():
  reset_bus()
  yield
  reset_bus()


def test_publish_subscribe_basic() -> None:
  bus = EventBus()
  received: list[BusMessage] = []

  async def run():
    sub = bus.subscribe(topic="test.topic")
    await bus.publish(BusMessage(topic="test.topic", payload={"x": 1}))
    msg = await sub.get(timeout=1.0)
    assert msg is not None
    received.append(msg)

  asyncio.run(run())
  assert len(received) == 1
  assert received[0].payload == {"x": 1}


def test_topic_filtering() -> None:
  bus = EventBus()

  async def run():
    sub = bus.subscribe(topic="org.report")
    await bus.publish(BusMessage(topic="job.report", payload={"a": 1}))
    await bus.publish(BusMessage(topic="org.report", payload={"b": 2}))
    msg = await sub.get(timeout=0.5)
    assert msg is not None
    assert msg.payload == {"b": 2}
    # Should not have the job.report
    msg2 = await sub.get(timeout=0.1)
    assert msg2 is None

  asyncio.run(run())


def test_wildcard_subscription() -> None:
  bus = EventBus()
  received: list[BusMessage] = []

  async def run():
    sub = bus.subscribe(topic="*")
    await bus.publish(BusMessage(topic="a", payload={"x": 1}))
    await bus.publish(BusMessage(topic="b", payload={"x": 2}))
    m1 = await sub.get(timeout=0.5)
    m2 = await sub.get(timeout=0.5)
    if m1:
      received.append(m1)
    if m2:
      received.append(m2)

  asyncio.run(run())
  assert len(received) == 2


def test_address_filtering() -> None:
  bus = EventBus()

  async def run():
    sub = bus.subscribe(topic="org.report", to_addr="verifier")
    # This message is addressed to "manager" — should NOT be received
    await bus.publish(BusMessage(topic="org.report", to_addr="manager", payload={"x": 1}))
    # This message is addressed to "verifier" — should be received
    await bus.publish(BusMessage(topic="org.report", to_addr="verifier", payload={"x": 2}))
    msg = await sub.get(timeout=0.5)
    assert msg is not None
    assert msg.payload == {"x": 2}

  asyncio.run(run())


def test_broadcast_no_address() -> None:
  bus = EventBus()

  async def run():
    sub = bus.subscribe(topic="test", to_addr="worker")
    # Broadcast (empty to_addr) should be received by everyone
    await bus.publish(BusMessage(topic="test", to_addr="", payload={"broadcast": True}))
    msg = await sub.get(timeout=0.5)
    assert msg is not None
    assert msg.payload == {"broadcast": True}

  asyncio.run(run())


def test_callback_subscriber() -> None:
  bus = EventBus()
  received: list[dict] = []

  async def my_callback(msg: BusMessage) -> None:
    received.append(msg.payload)

  async def run():
    bus.subscribe(topic="cb.test", callback=my_callback)
    await bus.publish(BusMessage(topic="cb.test", payload={"hello": "world"}))
    await asyncio.sleep(0.1)  # Let callback fire

  asyncio.run(run())
  assert received == [{"hello": "world"}]


def test_unsubscribe() -> None:
  bus = EventBus()

  async def run():
    sub = bus.subscribe(topic="unsub.test")
    await bus.publish(BusMessage(topic="unsub.test", payload={"x": 1}))
    msg = await sub.get(timeout=0.5)
    assert msg is not None

    sub.unsubscribe()
    await bus.publish(BusMessage(topic="unsub.test", payload={"x": 2}))
    msg2 = await sub.get(timeout=0.2)
    assert msg2 is None  # Should not receive after unsubscribe

  asyncio.run(run())


def test_recent_messages() -> None:
  bus = EventBus()

  async def run():
    for i in range(5):
      await bus.publish(BusMessage(topic="history", payload={"i": i}))

  asyncio.run(run())
  recent = bus.recent_messages(topic="history")
  assert len(recent) == 5
  assert recent[0].payload == {"i": 0}
  assert recent[4].payload == {"i": 4}


def test_global_bus_singleton() -> None:
  b1 = get_bus()
  b2 = get_bus()
  assert b1 is b2
