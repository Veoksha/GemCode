"""
In-memory event bus for agent-to-agent communication.

This replaces the Unix socket IPC dependency for intra-process messaging.
Agents publish events, other agents subscribe by topic/address.
Works without the Kaira daemon running.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class BusMessage:
  topic: str
  payload: dict[str, Any]
  from_addr: str = ""
  to_addr: str = ""  # empty = broadcast
  ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))


# Type for subscriber callbacks
Subscriber = Callable[[BusMessage], Awaitable[None] | None]


class EventBus:
  """
  In-memory async pub/sub bus for agent coordination.

  Features:
  - Topic-based subscription (e.g., "org.report", "job.report", "agent.heartbeat")
  - Address-based filtering (messages can target specific agents)
  - Wildcard subscriptions (subscribe to all topics with "*")
  - Non-blocking: publish never waits for subscribers
  - Thread-safe via asyncio primitives
  """

  def __init__(self) -> None:
    self._subscribers: dict[str, list[tuple[str | None, Subscriber]]] = {}
    # topic -> [(to_filter, callback), ...]
    self._wildcard_subscribers: list[tuple[str | None, Subscriber]] = []
    self._history: list[BusMessage] = []
    self._history_max = 500
    self._lock = asyncio.Lock()

  async def publish(self, msg: BusMessage) -> None:
    """Publish a message to all matching subscribers."""
    self._history.append(msg)
    if len(self._history) > self._history_max:
      self._history = self._history[-self._history_max:]

    # Collect matching subscribers
    targets: list[Subscriber] = []

    # Topic-specific subscribers
    subs = self._subscribers.get(msg.topic, [])
    for to_filter, cb in subs:
      if to_filter is None or to_filter == msg.to_addr or msg.to_addr == "":
        targets.append(cb)

    # Wildcard subscribers
    for to_filter, cb in self._wildcard_subscribers:
      if to_filter is None or to_filter == msg.to_addr or msg.to_addr == "":
        targets.append(cb)

    # Fire all subscribers (non-blocking, errors swallowed)
    for cb in targets:
      try:
        result = cb(msg)
        if asyncio.iscoroutine(result):
          asyncio.create_task(result)  # type: ignore[arg-type]
      except Exception:
        pass

  def subscribe(
    self,
    topic: str = "*",
    *,
    to_addr: str | None = None,
    callback: Subscriber | None = None,
  ) -> "Subscription":
    """
    Subscribe to messages.

    Args:
      topic: Topic to subscribe to, or "*" for all topics.
      to_addr: Only receive messages addressed to this address (None = all).
      callback: Async or sync callback invoked on each matching message.

    Returns:
      Subscription object with an async queue for pulling messages.
    """
    sub = Subscription(bus=self, topic=topic, to_addr=to_addr)

    async def _enqueue(msg: BusMessage) -> None:
      try:
        sub._queue.put_nowait(msg)
      except asyncio.QueueFull:
        # Drop oldest if full
        try:
          sub._queue.get_nowait()
        except asyncio.QueueEmpty:
          pass
        sub._queue.put_nowait(msg)

    effective_cb = callback or _enqueue

    if topic == "*":
      self._wildcard_subscribers.append((to_addr, effective_cb))
      sub._unsub_key = ("*", to_addr, effective_cb)
    else:
      self._subscribers.setdefault(topic, []).append((to_addr, effective_cb))
      sub._unsub_key = (topic, to_addr, effective_cb)

    return sub

  def unsubscribe(self, sub: "Subscription") -> None:
    """Remove a subscription."""
    if sub._unsub_key is None:
      return
    topic, to_addr, cb = sub._unsub_key
    if topic == "*":
      try:
        self._wildcard_subscribers.remove((to_addr, cb))
      except ValueError:
        pass
    else:
      subs = self._subscribers.get(topic, [])
      try:
        subs.remove((to_addr, cb))
      except ValueError:
        pass

  def recent_messages(self, topic: str | None = None, limit: int = 50) -> list[BusMessage]:
    """Get recent messages, optionally filtered by topic."""
    msgs = self._history if topic is None else [m for m in self._history if m.topic == topic]
    return msgs[-limit:]

  def publish_sync(self, msg: BusMessage) -> None:
    """Synchronous publish (schedules async delivery). Use from non-async contexts."""
    try:
      loop = asyncio.get_running_loop()
      loop.create_task(self.publish(msg))
    except RuntimeError:
      # No running loop — store in history only
      self._history.append(msg)
      if len(self._history) > self._history_max:
        self._history = self._history[-self._history_max:]


class Subscription:
  """Handle for a bus subscription with an async message queue."""

  def __init__(self, *, bus: EventBus, topic: str, to_addr: str | None) -> None:
    self._bus = bus
    self.topic = topic
    self.to_addr = to_addr
    self._queue: asyncio.Queue[BusMessage] = asyncio.Queue(maxsize=200)
    self._unsub_key: tuple | None = None

  async def get(self, timeout: float | None = None) -> BusMessage | None:
    """Wait for the next message, with optional timeout."""
    try:
      if timeout is None:
        return await self._queue.get()
      return await asyncio.wait_for(self._queue.get(), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
      return None

  def get_nowait(self) -> BusMessage | None:
    """Non-blocking get."""
    try:
      return self._queue.get_nowait()
    except asyncio.QueueEmpty:
      return None

  def unsubscribe(self) -> None:
    """Remove this subscription from the bus."""
    self._bus.unsubscribe(self)


# Global singleton bus (created lazily, shared across the process)
_global_bus: EventBus | None = None


def get_bus() -> EventBus:
  """Get or create the global in-process event bus."""
  global _global_bus
  if _global_bus is None:
    _global_bus = EventBus()
  return _global_bus


def reset_bus() -> None:
  """Reset the global bus (for testing)."""
  global _global_bus
  _global_bus = None
