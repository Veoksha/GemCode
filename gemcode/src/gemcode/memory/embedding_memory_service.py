"""
Embedding-backed memory service for GemCode.

This is a clean-room, local file-backed implementation of ADK's
`BaseMemoryService` that:
  - persists memory events (JSONL) to `.gemcode/memories.jsonl`
  - stores an embedding vector per memory record (MVP)
  - returns relevant memories via cosine similarity in `search_memory()`
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any
from typing import Iterable
from typing import Sequence

from google.adk.memory.base_memory_service import BaseMemoryService
from google.adk.memory.base_memory_service import SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types


_WORD_RE = re.compile(r"[A-Za-z]+")


def _words_lower(s: str) -> set[str]:
  return {w.lower() for w in _WORD_RE.findall(s or "")}


def _extract_text_parts(content: Any) -> list[str]:
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


def _cosine_similarity(a: list[float], b: list[float]) -> float:
  if not a or not b or len(a) != len(b):
    return -1.0
  dot = 0.0
  na = 0.0
  nb = 0.0
  for x, y in zip(a, b):
    dot += x * y
    na += x * x
    nb += y * y
  denom = math.sqrt(na) * math.sqrt(nb)
  if denom == 0:
    return -1.0
  return dot / denom


def _get_embedding_model() -> str:
  return os.environ.get("GEMCODE_EMBEDDINGS_MODEL", "models/gemini-embedding-2-preview")


def _get_embedding_api_key() -> str | None:
  return os.environ.get("GOOGLE_API_KEY")


async def _embed_texts(
  *,
  texts: Sequence[str],
  embedding_model: str,
) -> list[list[float]]:
  from google.genai import Client
  from google.genai.types import EmbedContentConfig

  client = Client(api_key=_get_embedding_api_key())
  config = EmbedContentConfig(auto_truncate=True)
  resp = await client.aio.models.embed_content(
    model=embedding_model,
    contents=list(texts),
    config=config,
  )
  return [list(e.values) for e in resp.embeddings]


class EmbeddingFileMemoryService(BaseMemoryService):
  """JSONL-backed memory service with embedding similarity search."""

  def __init__(
    self,
    memories_path: Path,
    *,
    embeddings_model: str | None = None,
    embedding_max_chars: int = 6000,
    embedding_batch_size: int = 16,
  ):
    self.memories_path = memories_path
    self.embeddings_model = embeddings_model or _get_embedding_model()
    self.embedding_max_chars = embedding_max_chars
    self.embedding_batch_size = embedding_batch_size

  def _ensure_parent(self) -> None:
    self.memories_path.parent.mkdir(parents=True, exist_ok=True)

  def _iter_records(self) -> Iterable[dict[str, Any]]:
    if not self.memories_path.is_file():
      return []
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

    # First pass: collect new texts to embed.
    new_records: list[dict[str, Any]] = []
    texts_to_embed: list[str] = []
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
      ts_out = ts if isinstance(ts, str) else None

      truncated = text[: self.embedding_max_chars]
      rec: dict[str, Any] = {
        "id": ev_id,
        "app_name": app_name,
        "user_id": user_id,
        "session_id": session_id,
        "author": author if isinstance(author, str) else None,
        "timestamp": ts_out,
        "text": text,
        "embedding_text": truncated,
        "embedding": None,
      }
      new_records.append(rec)
      texts_to_embed.append(truncated)
      existing_ids.add(ev_id)

    if not new_records:
      return

    # Embed in batches to avoid too-large requests.
    for i in range(0, len(new_records), self.embedding_batch_size):
      batch_records = new_records[i : i + self.embedding_batch_size]
      batch_texts = [r["embedding_text"] for r in batch_records]
      try:
        vectors = await _embed_texts(
          texts=batch_texts, embedding_model=self.embeddings_model
        )
        for r, vec in zip(batch_records, vectors):
          r["embedding"] = vec
      except Exception:
        # Best-effort: keep record but without embedding.
        for r in batch_records:
          r["embedding"] = None

    # Persist
    with self.memories_path.open("a", encoding="utf-8") as f:
      for rec in new_records:
        rec_out = dict(rec)
        rec_out.pop("embedding_text", None)
        f.write(json.dumps(rec_out, ensure_ascii=False) + "\n")

  async def search_memory(  # type: ignore[override]
    self,
    *,
    app_name: str,
    user_id: str,
    query: str,
  ) -> SearchMemoryResponse:
    response = SearchMemoryResponse()
    q = (query or "").strip()
    if not q:
      return response

    # Compute query embedding.
    try:
      q_vecs = await _embed_texts(
        texts=[q[: self.embedding_max_chars]], embedding_model=self.embeddings_model
      )
      q_vec = q_vecs[0]
    except Exception:
      # Fallback to naive keyword search if embedding fails.
      q_words = _words_lower(q)
      if not q_words:
        return response
      for rec in self._iter_records():
        if rec.get("app_name") != app_name or rec.get("user_id") != user_id:
          continue
        text = rec.get("text")
        if not isinstance(text, str):
          continue
        event_words = _words_lower(text)
        if event_words and any(w in event_words for w in q_words):
          content = types.Content(role="user", parts=[types.Part(text=text)])
          response.memories.append(
            MemoryEntry(
              content=content,
              author=rec.get("author") if isinstance(rec.get("author"), str) else None,
              timestamp=rec.get("timestamp") if isinstance(rec.get("timestamp"), str) else None,
            )
          )
      return response

    # Rank by cosine similarity.
    scored: list[tuple[float, dict[str, Any]]] = []
    for rec in self._iter_records():
      if rec.get("app_name") != app_name or rec.get("user_id") != user_id:
        continue
      vec = rec.get("embedding")
      text = rec.get("text")
      if not isinstance(vec, list) or not isinstance(text, str) or not vec:
        continue
      if not all(isinstance(x, (int, float)) for x in vec):
        continue
      v = [float(x) for x in vec]
      score = _cosine_similarity(q_vec, v)
      if score >= 0:
        scored.append((score, rec))

    scored.sort(key=lambda x: x[0], reverse=True)
    for _score, rec in scored[:12]:
      text = rec.get("text")
      if not isinstance(text, str):
        continue
      content = types.Content(role="user", parts=[types.Part(text=text)])
      response.memories.append(
        MemoryEntry(
          content=content,
          author=rec.get("author") if isinstance(rec.get("author"), str) else None,
          timestamp=rec.get("timestamp") if isinstance(rec.get("timestamp"), str) else None,
        )
      )

    return response

