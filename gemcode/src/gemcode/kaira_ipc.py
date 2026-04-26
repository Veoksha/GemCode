from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from gemcode.ide_protocol import IdeEmitter, make_event, make_response, parse_json_line


def default_ipc_socket_path(project_root: Path) -> Path:
  return project_root / ".gemcode" / "ipc.sock"


@dataclass
class IpcClient:
  writer: asyncio.StreamWriter
  emitter: IdeEmitter
  subscribed: bool = False
  topics: set[str] | None = None
  to_addrs: set[str] | None = None


class KairaIpcServer:
  """Unix-socket JSONL IPC for Kaira.

  Protocol (request objects; one JSON per line):
    {"type":"request","id":"...","action":"enqueue","prompt":"...","priority":0,"session_id":"..."}
    {"type":"request","id":"...","action":"subscribe"}  # starts streaming events

  Responses:
    {"type":"response","id":"...","ok":true,...}

  Events:
    {"type":"event","event":"job_queued",...}
    {"type":"event","event":"job_started",...}
    {"type":"event","event":"job_finished",...}
    {"type":"event","event":"job_failed",...}
    {"type":"event","event":"job_text_delta",...}
    {"type":"event","event":"bus_message","topic":"...","to":"...","payload":{...}}
  """

  def __init__(
    self,
    *,
    socket_path: Path,
    enqueue_fn: Callable[..., str],
    publish_hook: Callable[[dict[str, Any]], "asyncio.Future[None] | asyncio.Task[None] | Any"] | None = None,
    list_jobs_fn: Callable[..., list[dict[str, Any]]] | None = None,
    get_job_fn: Callable[..., dict[str, Any] | None] | None = None,
    cancel_job_fn: Callable[..., bool] | None = None,
    set_concurrency_fn: Callable[[int], int] | None = None,
  ) -> None:
    self.socket_path = Path(socket_path)
    self._enqueue_fn = enqueue_fn
    self._publish_hook = publish_hook
    self._list_jobs_fn = list_jobs_fn
    self._get_job_fn = get_job_fn
    self._cancel_job_fn = cancel_job_fn
    self._set_concurrency_fn = set_concurrency_fn
    self._server: asyncio.AbstractServer | None = None
    self._clients: set[IpcClient] = set()
    self._lock = asyncio.Lock()
    self._pending_confirmations: dict[str, asyncio.Future[bool]] = {}

  async def start(self) -> None:
    self.socket_path.parent.mkdir(parents=True, exist_ok=True)
    # Remove stale socket file if present.
    try:
      if self.socket_path.exists():
        self.socket_path.unlink()
    except Exception:
      pass

    self._server = await asyncio.start_unix_server(
      self._handle_client,
      path=str(self.socket_path),
    )
    try:
      os.chmod(self.socket_path, 0o600)
    except Exception:
      pass

  async def close(self) -> None:
    if self._server is not None:
      self._server.close()
      try:
        await self._server.wait_closed()
      except Exception:
        pass
      self._server = None
    try:
      if self.socket_path.exists():
        self.socket_path.unlink()
    except Exception:
      pass

  async def broadcast(self, msg: dict[str, Any]) -> None:
    # Broadcast only to subscribed clients.
    async with self._lock:
      clients = list(self._clients)
    for c in clients:
      if not c.subscribed:
        continue
      if not self._client_accepts(c, msg):
        continue
      try:
        c.emitter.send(msg)
      except Exception:
        # Drop dead client.
        await self._drop_client(c)

  def _client_accepts(self, c: IpcClient, msg: dict[str, Any]) -> bool:
    """
    Apply per-client subscription filters.

    We intentionally only filter `bus_message` events. Job lifecycle / streaming
    events remain broadcast to all subscribed clients so UIs behave predictably.
    """
    try:
      if msg.get("type") != "event":
        return True
      if str(msg.get("event") or "") != "bus_message":
        return True
      if c.topics:
        topic = str(msg.get("topic") or "")
        if topic not in c.topics:
          return False
      if c.to_addrs:
        to = str(msg.get("to") or "")
        if to not in c.to_addrs:
          return False
      return True
    except Exception:
      return True

  async def request_confirmation(
    self,
    *,
    job_id: str,
    session_id: str,
    tool: str,
    hint: str = "",
    timeout_s: float = 300.0,
  ) -> bool:
    """Ask a connected client to approve/deny a tool call."""
    req_id = f"confirm_{uuid.uuid4().hex[:12]}"
    fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    self._pending_confirmations[req_id] = fut
    await self.broadcast(
      job_event(
        "permission_request",
        request_id=req_id,
        job_id=job_id,
        session_id=session_id,
        tool=tool,
        hint=hint,
      )
    )
    try:
      return bool(await asyncio.wait_for(fut, timeout=timeout_s))
    except Exception:
      return False
    finally:
      self._pending_confirmations.pop(req_id, None)

  async def _drop_client(self, c: IpcClient) -> None:
    async with self._lock:
      if c in self._clients:
        self._clients.remove(c)
    try:
      c.writer.close()
      await c.writer.wait_closed()
    except Exception:
      pass

  async def _handle_client(
    self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
  ) -> None:
    client = IpcClient(writer=writer, emitter=IdeEmitter(stream=writer))
    async with self._lock:
      self._clients.add(client)

    try:
      while True:
        line = await reader.readline()
        if not line:
          break
        try:
          msg = parse_json_line(line.decode("utf-8", errors="replace").strip())
        except Exception as e:
          client.emitter.send(
            make_response(id="unknown", ok=False, error=f"invalid_message: {e}")
          )
          continue

        if msg.get("type") != "request":
          client.emitter.send(
            make_response(
              id=str(msg.get("id") or "unknown"),
              ok=False,
              error="expected request",
            )
          )
          continue

        req_id = str(msg.get("id") or "")
        action = str(msg.get("action") or "")
        if not req_id:
          client.emitter.send(
            make_response(id="unknown", ok=False, error="missing id")
          )
          continue

        if action == "subscribe":
          client.subscribed = True
          # Optional filters (apply only to bus_message events).
          topics = msg.get("topics")
          to_addrs = msg.get("to")
          try:
            if isinstance(topics, list):
              client.topics = {str(x) for x in topics if str(x).strip()}
            elif isinstance(topics, str) and topics.strip():
              client.topics = {topics.strip()}
            else:
              client.topics = None
          except Exception:
            client.topics = None
          try:
            if isinstance(to_addrs, list):
              client.to_addrs = {str(x) for x in to_addrs if str(x).strip()}
            elif isinstance(to_addrs, str) and to_addrs.strip():
              client.to_addrs = {to_addrs.strip()}
            else:
              client.to_addrs = None
          except Exception:
            client.to_addrs = None
          client.emitter.send(make_response(id=req_id, ok=True))
          continue

        if action == "enqueue":
          prompt = str(msg.get("prompt") or "").strip()
          if not prompt:
            client.emitter.send(
              make_response(id=req_id, ok=False, error="missing prompt")
            )
            continue
          try:
            pr = msg.get("priority", None)
            sid = str(msg.get("session_id") or "").strip()
            meta = msg.get("meta", None)
            if not isinstance(meta, dict):
              meta = None
            job_id = self._enqueue_fn(prompt=prompt, priority=pr, session_id=sid, meta=meta)
            client.emitter.send(make_response(id=req_id, ok=True, job_id=job_id))
          except Exception as e:
            client.emitter.send(
              make_response(id=req_id, ok=False, error=f"enqueue_failed: {e}")
            )
          continue

        if action == "publish":
          # General event bus: publish a bus_message to all subscribed clients.
          # Intended for multi-agent / multi-client coordination.
          try:
            topic = str(msg.get("topic") or "").strip()
            to = str(msg.get("to") or "").strip()
            from_addr = str(msg.get("from") or "").strip()
            payload = msg.get("payload", None)
            if not topic:
              client.emitter.send(make_response(id=req_id, ok=False, error="missing topic"))
              continue
            if payload is None:
              payload = {}
            ev = make_event(
              event="bus_message",
              topic=topic,
              to=to,
              from_addr=from_addr,
              payload=payload,
            )
            await self.broadcast(ev)
            # Optional: let the daemon react to bus messages (best-effort).
            if self._publish_hook is not None:
              try:
                maybe = self._publish_hook(ev)
                if asyncio.iscoroutine(maybe):
                  await maybe
              except Exception:
                pass
            client.emitter.send(make_response(id=req_id, ok=True))
          except Exception as e:
            client.emitter.send(make_response(id=req_id, ok=False, error=f"publish_failed: {e}"))
          continue

        if action == "list_jobs":
          if self._list_jobs_fn is None:
            client.emitter.send(make_response(id=req_id, ok=False, error="list_jobs not available"))
            continue
          try:
            limit = int(msg.get("limit") or 200)
            jobs = self._list_jobs_fn(limit=limit)
            client.emitter.send(make_response(id=req_id, ok=True, jobs=jobs))
          except Exception as e:
            client.emitter.send(make_response(id=req_id, ok=False, error=f"list_jobs_failed: {e}"))
          continue

        if action == "get_job":
          if self._get_job_fn is None:
            client.emitter.send(make_response(id=req_id, ok=False, error="get_job not available"))
            continue
          job_id = str(msg.get("job_id") or "").strip()
          if not job_id:
            client.emitter.send(make_response(id=req_id, ok=False, error="missing job_id"))
            continue
          try:
            job = self._get_job_fn(job_id=job_id)
            if job is None:
              client.emitter.send(make_response(id=req_id, ok=False, error="not_found"))
            else:
              client.emitter.send(make_response(id=req_id, ok=True, job=job))
          except Exception as e:
            client.emitter.send(make_response(id=req_id, ok=False, error=f"get_job_failed: {e}"))
          continue

        if action == "cancel_job":
          if self._cancel_job_fn is None:
            client.emitter.send(make_response(id=req_id, ok=False, error="cancel_job not available"))
            continue
          job_id = str(msg.get("job_id") or "").strip()
          if not job_id:
            client.emitter.send(make_response(id=req_id, ok=False, error="missing job_id"))
            continue
          try:
            ok = bool(self._cancel_job_fn(job_id=job_id))
            client.emitter.send(make_response(id=req_id, ok=ok))
          except Exception as e:
            client.emitter.send(make_response(id=req_id, ok=False, error=f"cancel_job_failed: {e}"))
          continue

        if action == "set_concurrency":
          if self._set_concurrency_fn is None:
            client.emitter.send(make_response(id=req_id, ok=False, error="set_concurrency not available"))
            continue
          try:
            n = int(msg.get("concurrency") or 0)
            new_n = int(self._set_concurrency_fn(n))
            client.emitter.send(make_response(id=req_id, ok=True, concurrency=new_n))
          except Exception as e:
            client.emitter.send(make_response(id=req_id, ok=False, error=f"set_concurrency_failed: {e}"))
          continue

        if action == "permission_response":
          request_id = str(msg.get("request_id") or "").strip()
          confirmed = bool(msg.get("confirmed"))
          fut = self._pending_confirmations.get(request_id)
          if fut is None or fut.done():
            client.emitter.send(make_response(id=req_id, ok=False, error="unknown request_id"))
          else:
            try:
              fut.set_result(confirmed)
            except Exception:
              pass
            client.emitter.send(make_response(id=req_id, ok=True))
          continue

        client.emitter.send(
          make_response(id=req_id, ok=False, error=f"unknown action: {action}")
        )
    finally:
      await self._drop_client(client)


def job_event(event: str, **payload: Any) -> dict[str, Any]:
  return make_event(event=event, **payload)

