"""
Disk-backed storage for oversized tool outputs.

Why:
- Large tool outputs (stdout, file contents, web pages) are the biggest driver of
  context bloat and cache misses in long agent sessions.
- Instead of truncating blobs inline (which still mutates history repeatedly),
  we store the full payload on disk and replace it with a stable reference +
  short preview. This matches Reference UI "tool result storage" pattern.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

_REF_PREFIX = "tool_result:"
_REPL_STATE_KEY = "gemcode:tool_replacement_state"


def _stable_key(tool_name: str, args: dict[str, Any] | None, seq: int | None) -> str:
  """
  Build a stable key for replacement decisions.

  ADK does not expose tool_use_id directly to callbacks in all versions, so we use:
  - per-turn tool sequence number (preferred when available)
  - tool name
  - a stable hash of args (best-effort)
  """
  import json
  try:
    args_s = json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
  except Exception:
    args_s = str(args or {})
  b = (f"{seq or 0}:{tool_name}:{args_s}").encode("utf-8", errors="replace")
  return _sha256_bytes(b)


def maybe_offload_tool_result_stable(
  *,
  project_root: Path,
  tool_name: str,
  args: dict[str, Any] | None,
  payload: Any,
  max_inline_chars: int,
  state: dict[str, Any] | None,
  seq: int | None,
) -> tuple[Any, bool]:
  """
  Stable offload wrapper.

  - If we've already processed an identical tool call in this session, we re-apply
    the exact same replacement structure to preserve prompt byte stability.
  - Otherwise, we apply offload and remember the replacement result.
  """
  if state is None:
    return maybe_offload_tool_result(
      project_root=project_root,
      tool_name=tool_name,
      payload=payload,
      max_inline_chars=max_inline_chars,
    )

  repl_state = state.get(_REPL_STATE_KEY)
  if not isinstance(repl_state, dict):
    repl_state = {}
    state[_REPL_STATE_KEY] = repl_state

  key = _stable_key(tool_name, args, seq)
  if key in repl_state:
    return repl_state[key], False

  new_payload, did = maybe_offload_tool_result(
    project_root=project_root,
    tool_name=tool_name,
    payload=payload,
    max_inline_chars=max_inline_chars,
  )
  if did:
    repl_state[key] = new_payload
  else:
    # Freeze "no replacement" decision too (prevents later shape drift).
    repl_state[key] = payload
  return new_payload, did


def _store_dir(project_root: Path) -> Path:
  d = project_root / ".gemcode" / "tool-results"
  d.mkdir(parents=True, exist_ok=True)
  return d


def _sha256_bytes(b: bytes) -> str:
  h = hashlib.sha256()
  h.update(b)
  return h.hexdigest()


def _preview(text: str, max_chars: int) -> str:
  if max_chars <= 0:
    return ""
  if len(text) <= max_chars:
    return text
  if max_chars <= 40:
    return text[:max_chars]
  return text[: max_chars - 20] + "\n… [offloaded; preview truncated]\n"


def offload_text(
  *,
  project_root: Path,
  tool_name: str,
  field: str,
  text: str,
  preview_max_chars: int,
) -> dict[str, Any]:
  """
  Persist `text` to disk and return a compact reference dict.

  The ref is content-addressed (sha256 of bytes) so repeated identical outputs
  map to the same ref, improving cache stability.
  """
  b = text.encode("utf-8", errors="replace")
  sha = _sha256_bytes(b)
  ref = f"{_REF_PREFIX}{sha}"
  p = _store_dir(project_root) / f"{sha}.txt"
  if not p.exists():
    # Write once; keep deterministic content for stable cache behavior.
    p.write_bytes(b)
    meta = {
      "ref": ref,
      "sha256": sha,
      "tool": tool_name,
      "field": field,
      "bytes": len(b),
      "chars": len(text),
      "created_at": int(time.time()),
    }
    ( _store_dir(project_root) / f"{sha}.json" ).write_text(
      json.dumps(meta, ensure_ascii=False, indent=2),
      encoding="utf-8",
      errors="replace",
    )
  return {
    "offloaded": True,
    "ref": ref,
    "preview": _preview(text, preview_max_chars),
    "chars": len(text),
    "hint": "Use load_tool_result(ref) to view the full content.",
  }


def maybe_offload_tool_result(
  *,
  project_root: Path,
  tool_name: str,
  payload: Any,
  max_inline_chars: int,
) -> tuple[Any, bool]:
  """
  Walk a tool-result payload and offload large text fields.

  Returns (new_payload, changed).
  """
  if max_inline_chars <= 0:
    return payload, False

  changed = False

  def _walk(obj: Any, *, field: str) -> Any:
    nonlocal changed
    if isinstance(obj, str) and len(obj) > max_inline_chars:
      changed = True
      return offload_text(
        project_root=project_root,
        tool_name=tool_name,
        field=field,
        text=obj,
        preview_max_chars=max_inline_chars,
      )

    if isinstance(obj, list):
      out_list: list[Any] = []
      for i, item in enumerate(obj):
        out_list.append(_walk(item, field=f"{field}[{i}]"))
      if out_list != obj:
        changed = True
      return out_list

    if isinstance(obj, dict):
      out_dict: dict[str, Any] = {}
      for k, v in obj.items():
        out_dict[k] = _walk(v, field=str(k))
      if out_dict != obj:
        changed = True
      return out_dict

    return obj

  # Only dict payloads are expected from our tools, but handle any.
  return _walk(payload, field="payload"), changed


def load_tool_result_text(
  *,
  project_root: Path,
  ref: str,
  max_chars: int = 40_000,
  tail: bool = True,
) -> dict[str, Any]:
  if not isinstance(ref, str) or not ref.startswith(_REF_PREFIX):
    return {"error": "Invalid ref. Expected 'tool_result:<sha256>'."}
  sha = ref[len(_REF_PREFIX) :].strip()
  if not sha or any(c not in "0123456789abcdef" for c in sha) or len(sha) < 32:
    return {"error": "Invalid ref sha."}
  p = _store_dir(project_root) / f"{sha}.txt"
  if not p.exists():
    return {"error": f"Not found: {ref}"}
  text = p.read_text(encoding="utf-8", errors="replace")
  truncated = False
  if max_chars is not None and isinstance(max_chars, int) and max_chars > 0:
    if len(text) > max_chars:
      truncated = True
      text = ("… [truncated; showing tail]\n" + text[-max_chars:]) if tail else (text[:max_chars] + "\n… [truncated]")
  return {"ref": ref, "text": text, "truncated": truncated, "chars": len(text)}

