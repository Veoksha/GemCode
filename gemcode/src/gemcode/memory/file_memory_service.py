"""
Persistent, clean-room memory service for GemCode.

This complements ADK's memory integration by providing a file-backed
implementation of `BaseMemoryService` so memory survives across CLI runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from google.adk.memory.base_memory_service import BaseMemoryService
from google.adk.memory.base_memory_service import SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types


_WORD_RE = re.compile(r"[A-Za-z]+")


def _words_lower(s: str) -> set[str]:
  return {w.lower() for w in _WORD_RE.findall(s or "")}


def _extract_text_parts(content: Any) -> list[str]:
  # `google.genai.types.Content.parts` is a list of Part-like objects.
  # We store only text parts for retrieval.
  try:
    parts = getattr(content, "parts", None)
    if not parts:
      return []
    out: list[str] = []
    for p in parts:
      t = getattr(p, "text", None)
      if isinstance(t, str) and t.strip():
        out.append(t.strip())
    return out
  except Exception:
    return []


def _concat_text(content: Any) -> str:
  pieces = _extract_text_parts(content)
  if not pieces:
    return ""
  return "\n".join(pieces)


class FileMemoryService(BaseMemoryService):
  """JSONL-backed memory service with naive keyword matching."""

  def __init__(self, memories_path: Path):
    self.memories_path = memories_path

  def _ensure_parent(self) -> None:
    self.memories_path.parent.mkdir(parents=True, exist_ok=True)

  def _iter_records(self) -> Iterable[dict[str, Any]]:
    if not self.memories_path.is_file():
      return []
    # Best-effort JSONL parse; skip corrupt lines.
    with self.memories_path.open("r", encoding="utf-8") as f:
      for line in f:
        line = line.strip()
        if not line:
          continue
        try:
          yield json.loads(line)
        except json.JSONDecodeError:
          continue

  async def add_session_to_memory(self, session) -> None:  # type: ignore[override]
    await self.add_events_to_memory(
      app_name=session.app_name,
      user_id=session.user_id,
      session_id=session.id,
      events=session.events,
    )

  async def add_events_to_memory(  # type: ignore[override]
    self,
    *,
    app_name: str,
    user_id: str,
    events,
    session_id: str | None = None,
    custom_metadata: Any = None,
  ) -> None:
    _ = custom_metadata
    self._ensure_parent()

    existing_ids: set[str] = set()
    for r in self._iter_records():
      if r.get("app_name") == app_name and r.get("user_id") == user_id:
        mid = r.get("id")
        if isinstance(mid, str) and mid:
          existing_ids.add(mid)

    to_append: list[dict[str, Any]] = []
    for ev in events:
      author = getattr(ev, "author", None)
      content = getattr(ev, "content", None)
      if content is None:
        continue
      text = _concat_text(content)
      if not text.strip():
        continue

      ev_id = getattr(ev, "id", None)
      if not isinstance(ev_id, str) or not ev_id:
        continue
      if ev_id in existing_ids:
        continue

      ts = getattr(ev, "timestamp", None)
      # ADK event.timestamp is typically a string; preserve best-effort.
      ts_out = ts if isinstance(ts, str) else None

      to_append.append(
        {
          "id": ev_id,
          "app_name": app_name,
          "user_id": user_id,
          "session_id": session_id,
          "author": author,
          "timestamp": ts_out,
          "text": text,
        }
      )
      existing_ids.add(ev_id)

    if not to_append:
      return

    with self.memories_path.open("a", encoding="utf-8") as f:
      for rec in to_append:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

  async def search_memory(  # type: ignore[override]
    self, *, app_name: str, user_id: str, query: str
  ) -> SearchMemoryResponse:
    response = SearchMemoryResponse()
    query_words = _words_lower(query)
    if not query_words:
      return response

    for rec in self._iter_records():
      if rec.get("app_name") != app_name or rec.get("user_id") != user_id:
        continue
      text = rec.get("text")
      if not isinstance(text, str) or not text:
        continue
      event_words = _words_lower(text)
      if not event_words:
        continue
      if any(w in event_words for w in query_words):
        ts = rec.get("timestamp")
        author = rec.get("author")
        # Recreate MemoryEntry with a single text part.
        content = types.Content(
          role="user",
          parts=[types.Part(text=text)],
        )
        response.memories.append(
          MemoryEntry(
            content=content,
            author=author if isinstance(author, str) else None,
            timestamp=ts if isinstance(ts, str) else None,
          )
        )

    return response

