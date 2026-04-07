"""
Modality tool injection for GemCode.

interactive CLI–style: outer loop + inner tool orchestration remains ADK-driven,
but we choose which tools to expose based on user flags / prompt heuristics.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig


def _get_embedding_client():
  # google-genai picks up credentials from GOOGLE_API_KEY by default, but we
  # pass explicitly so this works in tests/processes with different env.
  from google.genai import Client

  api_key = os.environ.get("GOOGLE_API_KEY")
  return Client(api_key=api_key)


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


def _chunk_text(text: str, *, chunk_size: int = 1200, max_chunks: int = 8) -> list[str]:
  t = (text or "").strip()
  if not t:
    return []
  # Simple fixed-size chunks (MVP): fast, deterministic, and good enough for
  # semantic retrieval at small scales.
  out: list[str] = []
  for i in range(0, len(t), chunk_size):
    if len(out) >= max_chunks:
      break
    out.append(t[i : i + chunk_size])
  return out


async def semantic_search_files(
  query: str,
  path_glob: str = "**/*",
  *,
  max_files: int = 25,
  max_chunks_per_file: int = 6,
  max_total_chunks: int = 40,
  max_file_bytes: int = 200_000,
  max_results: int = 8,
  embedding_model: str | None = None,
  project_root: str | None = None,
) -> dict[str, Any]:
  """
  Embeddings-powered semantic search across files under the project root.

  Notes:
  - This MVP performs per-call embedding (no persistent vector index).
  - It is intentionally bounded (max_files/max_total_chunks) to limit API
    calls and latency.
  """
  if not isinstance(query, str) or not query.strip():
    return {"error": "query must be a non-empty string"}

  root = Path(project_root).resolve() if project_root else None
  if root is None:
    # When invoked as a GemCode tool, `project_root` is supplied by ADK via
    # closure (see build_extra_tools).
    return {"error": "project_root not provided"}

  if ".." in path_glob or path_glob.startswith("/"):
    return {"error": "Invalid path_glob"}

  embedding_model = embedding_model or os.environ.get(
    "GEMCODE_EMBEDDINGS_MODEL", "models/gemini-embedding-2-preview"
  )

  # Collect candidate chunks.
  chunks: list[str] = []
  chunk_meta: list[dict[str, str]] = []

  files_seen = 0
  for fp in root.glob(path_glob):
    if files_seen >= max_files:
      break
    if not fp.is_file():
      continue
    files_seen += 1

    try:
      data = fp.read_bytes()
    except OSError:
      continue
    if len(data) > max_file_bytes:
      data = data[:max_file_bytes]
    try:
      text = data.decode("utf-8", errors="ignore")
    except Exception:
      continue

    file_chunks = _chunk_text(text, max_chunks=max_chunks_per_file)
    if not file_chunks:
      continue
    for c in file_chunks:
      if len(chunks) >= max_total_chunks:
        break
      chunks.append(c)
      rel = fp.resolve().relative_to(root)
      chunk_meta.append({"path": str(rel)})
    if len(chunks) >= max_total_chunks:
      break

  if not chunks:
    return {"query": query, "matches": [], "backend": "embeddings"}

  client = _get_embedding_client()

  # Embed query and chunks.
  try:
    from google.genai.types import EmbedContentConfig

    config = EmbedContentConfig()
    q_emb = await client.aio.models.embed_content(
      model=embedding_model,
      contents=[query],
      config=config,
    )
    q_vec = list(q_emb.embeddings[0].values)

    c_emb = await client.aio.models.embed_content(
      model=embedding_model,
      contents=chunks,
      config=config,
    )
    c_vecs = [list(e.values) for e in c_emb.embeddings]
  except Exception as e:
    return {"error": f"embedding failed: {type(e).__name__}: {e}"}

  scored: list[tuple[float, int]] = []
  for i, vec in enumerate(c_vecs):
    score = _cosine_similarity(q_vec, vec)
    scored.append((score, i))

  scored.sort(key=lambda x: x[0], reverse=True)
  matches: list[dict[str, Any]] = []
  for score, idx in scored[: max_results]:
    if score < 0:
      continue
    rel = chunk_meta[idx]["path"]
    snippet = chunks[idx][:500].replace("\n", " ")
    matches.append({"path": rel, "snippet": snippet, "score": score})

  return {"query": query, "backend": "embeddings", "matches": matches}


def build_extra_tools(cfg: GemCodeConfig) -> list[Any]:
  """Return ADK tool unions to expose for enabled modalities."""
  extra: list[Any] = []

  # ── Web search (standalone, no full deep_research needed) ────────────────
  # enable_web_search=True adds google_search alone.
  # enable_deep_research=True adds google_search + url_context + optional maps.
  # If both are on, avoid adding google_search twice.
  web_search_added = False
  if getattr(cfg, "enable_web_search", False) and not getattr(cfg, "enable_deep_research", False):
    try:
      from google.adk.tools import google_search
      extra.append(google_search)
      web_search_added = True
    except Exception:
      pass

  if getattr(cfg, "enable_deep_research", False):
    from google.adk.tools import google_search, url_context
    if not web_search_added:
      extra.append(google_search)
    extra.append(url_context)
    # Google Maps grounding can be incompatible with other built-in tools
    # (e.g., google_search) depending on the request/model tooling layer.
    # Make it opt-in so deep-research stays reliable by default.
    if getattr(cfg, "enable_maps_grounding", False):
      from google.adk.tools.google_maps_grounding_tool import google_maps_grounding

      extra.append(google_maps_grounding)

  if getattr(cfg, "enable_embeddings", False):
    # Provide a closure so the embedding tool can resolve project_root.
    async def _semantic_search_files(
      query: str,
      path_glob: str = "**/*",
      *,
      max_files: int = 25,
      max_chunks_per_file: int = 6,
      max_total_chunks: int = 40,
      max_file_bytes: int = 200_000,
      max_results: int = 8,
      embedding_model: str | None = None,
    ):
      return await semantic_search_files(
        query,
        path_glob,
        max_files=max_files,
        max_chunks_per_file=max_chunks_per_file,
        max_total_chunks=max_total_chunks,
        max_file_bytes=max_file_bytes,
        max_results=max_results,
        embedding_model=embedding_model,
        project_root=str(cfg.project_root),
      )

    _semantic_search_files.__name__ = "semantic_search_files"
    extra.append(_semantic_search_files)

  return extra

