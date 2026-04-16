from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _enabled() -> bool:
  return os.environ.get("GEMCODE_VEOMEM", "").strip().lower() in ("1", "true", "yes", "on")


def make_veomem_tools(cfg) -> list[Any]:
  """
  VeoMem recall tools (token-efficient, 3-step retrieval workflow).

  Exposes a compact flow to the agent:
  1) `veomem_search` returns a small index of matching observation IDs
  2) `veomem_timeline` fetches compact context around a specific anchor ID
  3) `veomem_get_observations` fetches full text for a small set of IDs
  """
  if not _enabled():
    return []

  project_root = Path(getattr(cfg, "project_root"))

  try:
    from veomem.store import get_observations as _get_observations
    from veomem.store import search as _search
    from veomem.store import timeline as _timeline
  except Exception:
    return []

  def veomem_search(
    query: str,
    limit: int = 10,
    kind: str | None = None,
    wing: str | None = None,
    room: str | None = None,
  ) -> dict[str, Any]:
    """
    Search VeoMem for relevant prior tool observations.

    Returns a small index of hits (IDs + snippets). Typical usage:
    - call `veomem_timeline()` for one promising anchor
    - then call `veomem_get_observations()` for a small set of IDs
    """
    if not isinstance(query, str) or not query.strip():
      return {"error": "query must be a non-empty string"}
    return _search(
      project_root,
      query=str(query),
      limit=int(limit),
      kind=str(kind) if kind else None,
      wing=str(wing) if wing else None,
      room=str(room) if room else None,
    )

  def veomem_timeline(
    id: int,
    window_ms: int = 120000,
    limit: int = 40,
  ) -> dict[str, Any]:
    """
    Fetch compact context around a specific VeoMem observation id.

    This is the "timeline" step: it returns neighbors around an anchor.
    The agent then decides which IDs to fetch in full.
    """
    return _timeline(
      project_root,
      observation_id=int(id),
      window_ms=int(window_ms),
      limit=int(limit),
    )

  def veomem_get_observations(
    ids: str,
    max_chars: int = 8000,
  ) -> dict[str, Any]:
    """
    Fetch full observation text for selected VeoMem observation IDs.

    ids:
      Comma-separated list, e.g. "12,13,21"

    The returned `text` is truncated to `max_chars` per observation.
    """
    if not isinstance(ids, str) or not ids.strip():
      return {"error": "ids must be a non-empty comma-separated string"}

    parts = [x.strip() for x in ids.split(",") if x.strip()]
    id_list: list[int] = []
    for p in parts:
      try:
        id_list.append(int(p))
      except Exception:
        pass

    if not id_list:
      return {"error": "no valid integer ids found"}

    rows = _get_observations(project_root, ids=id_list)
    out: list[dict[str, Any]] = []
    for o in rows:
      t = (o.text or "").strip().replace("\n", " ")
      truncated = False
      if len(t) > int(max_chars):
        t = t[: int(max_chars)].rstrip() + "…"
        truncated = True
      out.append(
        {
          "id": int(o.id),
          "kind": str(o.kind),
          "title": str(o.title),
          "wing": str(o.wing),
          "room": str(o.room),
          "text": t,
          "truncated": truncated,
        }
      )

    return {"ok": True, "results": out, "count": len(out)}

  veomem_search.__name__ = "veomem_search"
  veomem_timeline.__name__ = "veomem_timeline"
  veomem_get_observations.__name__ = "veomem_get_observations"

  return [veomem_search, veomem_timeline, veomem_get_observations]
import os
from pathlib import Path
from typing import Any


def _enabled() -> bool:
  return os.environ.get("GEMCODE_VEOMEM", "").strip().lower() in ("1", "true", "yes", "on")


def make_veomem_tools(cfg) -> list[Any]:
  """
  VeoMem recall tools (compact 3-step retrieval workflow).

  Exposes a token-efficient 3-step flow to the agent:
  1) `veomem_search` returns an index of matching observation IDs
  2) `veomem_timeline` fetches compact context around a specific anchor ID
  3) `veomem_get_observations` fetches full text for a small set of IDs
  """
  if not _enabled():
    return []

  project_root = Path(getattr(cfg, "project_root"))

  try:
    from veomem.store import get_observations as _get_observations
    from veomem.store import search as _search
    from veomem.store import timeline as _timeline
  except Exception:
    return []

  def veomem_search(
    query: str,
    limit: int = 10,
    kind: str | None = None,
    wing: str | None = None,
    room: str | None = None,
  ) -> dict[str, Any]:
    """
    Search VeoMem for relevant prior tool observations (FTS5/BM25).

    Returns a small "index" of hits (IDs + snippets). Prefer:
    - call `veomem_timeline()` for one promising anchor
    - then call `veomem_get_observations()` for a small set of IDs
    """
    if not isinstance(query, str) or not query.strip():
      return {"error": "query must be a non-empty string"}
    return _search(
      project_root,
      query=str(query),
      limit=int(limit),
      kind=str(kind) if kind else None,
      wing=str(wing) if wing else None,
      room=str(room) if room else None,
    )

  def veomem_timeline(
    id: int,
    window_ms: int = 120000,
    limit: int = 40,
  ) -> dict[str, Any]:
    """
    Fetch compact context around a specific VeoMem observation id.

    This is the "timeline" step: it returns neighbors around an anchor
    (usually the agent should then decide which IDs to fetch in full).
    """
    return _timeline(
      project_root,
      observation_id=int(id),
      window_ms=int(window_ms),
      limit=int(limit),
    )

  def veomem_get_observations(
    ids: str,
    max_chars: int = 8000,
  ) -> dict[str, Any]:
    """
    Fetch full observation text for selected VeoMem observation IDs.

    ids:
      Comma-separated list, e.g. "12,13,21"

    The returned `text` is truncated to `max_chars` per observation.
    """
    if not isinstance(ids, str) or not ids.strip():
      return {"error": "ids must be a non-empty comma-separated string"}
    parts = [x.strip() for x in ids.split(",") if x.strip()]
    id_list: list[int] = []
    for p in parts:
      try:
        id_list.append(int(p))
      except Exception:
        pass
    if not id_list:
      return {"error": "no valid integer ids found"}

    rows = _get_observations(project_root, ids=id_list)
    out: list[dict[str, Any]] = []
    for o in rows:
      t = (o.text or "").strip().replace("\n", " ")
      truncated = False
      if len(t) > int(max_chars):
        t = t[: int(max_chars)].rstrip() + "…"
        truncated = True
      out.append(
        {
          "id": int(o.id),
          "kind": str(o.kind),
          "title": str(o.title),
          "wing": str(o.wing),
          "room": str(o.room),
          "text": t,
          "truncated": truncated,
        }
      )
    return {"ok": True, "results": out, "count": len(out)}

  veomem_search.__name__ = "veomem_search"
  veomem_timeline.__name__ = "veomem_timeline"
  veomem_get_observations.__name__ = "veomem_get_observations"

  return [veomem_search, veomem_timeline, veomem_get_observations]

