from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator

from gemcode.ide_protocol import IdeEmitter, make_response, parse_json_line


@dataclass
class KairaIpcClient:
  reader: asyncio.StreamReader
  writer: asyncio.StreamWriter
  emitter: IdeEmitter

  @classmethod
  async def connect(cls, *, socket_path: str) -> "KairaIpcClient":
    reader, writer = await asyncio.open_unix_connection(socket_path)
    return cls(reader=reader, writer=writer, emitter=IdeEmitter(stream=writer))

  async def close(self) -> None:
    try:
      self.writer.close()
      await self.writer.wait_closed()
    except Exception:
      pass

  async def request(self, *, action: str, **payload: Any) -> dict[str, Any]:
    req_id = f"req_{uuid.uuid4().hex[:12]}"
    msg = {"type": "request", "id": req_id, "action": action}
    msg.update(payload)
    self.emitter.send(msg)
    # Wait for matching response.
    while True:
      line = await self.reader.readline()
      if not line:
        return make_response(id=req_id, ok=False, error="ipc_eof")
      obj = parse_json_line(line.decode("utf-8", errors="replace").strip())
      if obj.get("type") == "response" and str(obj.get("id") or "") == req_id:
        return obj
      # Other messages (events) are ignored here; caller should use iter_messages.

  async def subscribe(
    self,
    *,
    topics: list[str] | None = None,
    to: list[str] | None = None,
  ) -> dict[str, Any]:
    """
    Subscribe to server events.

    Optional filters apply only to `bus_message` events; job_* events are always
    delivered to all subscribed clients.
    """
    payload: dict[str, Any] = {}
    if topics:
      payload["topics"] = list(topics)
    if to:
      payload["to"] = list(to)
    return await self.request(action="subscribe", **payload)

  async def publish(
    self,
    *,
    topic: str,
    payload: Any,
    to: str = "",
    from_addr: str = "",
  ) -> dict[str, Any]:
    """Publish a `bus_message` event to all subscribed clients."""
    return await self.request(
      action="publish",
      topic=str(topic),
      to=str(to or ""),
      **({"from": str(from_addr)} if from_addr else {}),
      payload=payload,
    )

  async def iter_messages(self) -> AsyncIterator[dict[str, Any]]:
    while True:
      line = await self.reader.readline()
      if not line:
        return
      obj = parse_json_line(line.decode("utf-8", errors="replace").strip())
      yield obj

