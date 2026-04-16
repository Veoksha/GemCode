from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _enabled() -> bool:
  return os.environ.get("GEMCODE_VEOMEM", "").strip().lower() in ("1", "true", "yes", "on")


def _try_import():
  try:
    from veomem.store import add_observation  # type: ignore[import-not-found]
    return add_observation
  except Exception:
    return None


def _summarize_tool_result(result: dict[str, Any]) -> str:
  if not isinstance(result, dict):
    return ""
  if result.get("error"):
    e = str(result.get("error"))
    return f"error: {e[:800]}"

  def _clip_str(x: Any, n: int) -> str:
    if x is None:
      return ""
    s = str(x)
    s = s.strip()
    if len(s) <= n:
      return s
    return s[:n].rstrip() + "…"

  parts: list[str] = []
  for k in ("exit_code", "path", "backup_path", "count", "chars_before", "chars_after"):
    if k in result:
      parts.append(f"{k}={result.get(k)!r}")
  for k in ("stdout", "stderr"):
    v = result.get(k)
    if isinstance(v, str) and v.strip():
      parts.append(f"{k}={_clip_str(v, 800)}")

  # Web search results can be high-signal but are structured; make them searchable.
  try:
    results = result.get("results")
    if isinstance(results, list) and results:
      pieces: list[str] = []
      for r in results[:5]:
        if not isinstance(r, dict):
          continue
        title = _clip_str(r.get("title"), 120)
        url = _clip_str(r.get("url"), 140)
        snippet = _clip_str(r.get("snippet"), 180)
        if title or url or snippet:
          pieces.append(f"{title} ({url}) {snippet}".strip())
      if pieces:
        parts.append("results=[" + " | ".join(pieces) + "]")
  except Exception:
    pass

  return " ".join(parts).strip()


def record_tool_use(
  project_root: Path,
  *,
  session_id: str | None,
  tool_name: str,
  args: dict[str, Any],
  result: dict[str, Any],
  paths: list[str] | None = None,
) -> None:
  if not _enabled():
    return
  add_observation = _try_import()
  if add_observation is None:
    return

  touched = list(paths or [])
  # Heuristic: record read_file path if present.
  try:
    p = (args or {}).get("path")
    if isinstance(p, str) and p.strip():
      touched.append(p.strip())
  except Exception:
    pass
  touched = list(dict.fromkeys([p for p in touched if isinstance(p, str) and p.strip()]))[:50]

  text = _summarize_tool_result(result)
  if not text:
    # Keep small but non-empty to be searchable.
    text = json.dumps({"ok": not bool(result.get("error")), "tool": tool_name}, ensure_ascii=False)

  try:
    add_observation(
      project_root,
      kind="tool",
      title=tool_name,
      text=text,
      session_id=session_id,
      tool_name=tool_name,
      paths=touched,
      extra={"args_keys": sorted(list((args or {}).keys()))[:40]},
    )
  except Exception:
    return


def record_turn_summary(project_root: Path, *, session_id: str | None, text: str) -> None:
  if not _enabled():
    return
  add_observation = _try_import()
  if add_observation is None:
    return
  t = (text or "").strip()
  if not t:
    return
  try:
    add_observation(
      project_root,
      kind="summary",
      title="turn_summary",
      text=t[:8000],
      session_id=session_id,
      tool_name=None,
      paths=[],
      extra={},
    )
  except Exception:
    return

