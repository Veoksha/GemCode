"""
Bounded tool output and soft prompt-size limits (interactive CLI–style context hygiene).

- Truncate oversized tool result dicts before they enter history (`after_tool`).
- Before each LLM call, trim oldest text parts until estimated char total is under
  GEMCODE_MAX_CONTEXT_CHARS (does not remove whole Content rows; preserves
  tool-call / function-response structure).
"""

from __future__ import annotations

from typing import Any

from gemcode.config import GemCodeConfig

_CONTEXT_MARK = "\n… [truncated by GemCode context budget]"

# Keep the marker short so even small `max_str` values can still contain it.
_TOOL_TRUNC_MARK = "[truncated]"
_MIN_TEXT_AFTER_SHRINK = 800
_MIN_TOOL_AFTER_SHRINK = 200
_MAX_MATCH_ITEMS = 300


def estimate_obj_string_chars(obj: Any, *, max_total: int = 200_000, _depth: int = 0) -> int:
  """Rough estimate of total string chars inside nested tool payloads.

  This intentionally favors speed over precision.
  """
  if obj is None or max_total <= 0 or _depth > 5:
    return 0
  if isinstance(obj, str):
    return min(len(obj), max_total)

  total = 0
  if isinstance(obj, list):
    for item in obj[:_MAX_MATCH_ITEMS]:
      total += estimate_obj_string_chars(item, max_total=max_total - total, _depth=_depth + 1)
      if total >= max_total:
        return total
    return total

  if isinstance(obj, dict):
    for v in obj.values():
      total += estimate_obj_string_chars(v, max_total=max_total - total, _depth=_depth + 1)
      if total >= max_total:
        return total
    return total

  return 0


def estimate_part_payload_chars(part: Any) -> int:
  """Estimate payload size for both visible text and tool results."""
  total = 0
  try:
    t = getattr(part, "text", None)
    if isinstance(t, str):
      total += len(t)
  except Exception:
    pass

  # Function responses are used for HITL confirmations; these are usually small,
  # but we account for them for completeness.
  try:
    fr = getattr(part, "function_response", None)
    resp = getattr(fr, "response", None)
    total += estimate_obj_string_chars(resp)
  except Exception:
    pass

  # Tool responses are where most of the (possibly huge) payload lives.
  try:
    tr = getattr(part, "tool_response", None)
    resp = getattr(tr, "response", None)
    total += estimate_obj_string_chars(resp)
  except Exception:
    pass

  return total


def estimate_contents_text_chars(contents: Any) -> int:
  """Rough sum of visible text and tool payload string chars."""
  if not contents:
    return 0
  total = 0
  try:
    for c in contents:
      for p in getattr(c, "parts", None) or []:
        total += estimate_part_payload_chars(p)
  except Exception:
    return total
  return total


def _ordered_text_part_refs(contents: Any) -> list[tuple[int, int, Any]]:
  """(content_idx, part_idx, part) for parts with non-empty text, oldest first."""
  out: list[tuple[int, int, Any]] = []
  try:
    for ci, c in enumerate(contents):
      parts = getattr(c, "parts", None) or []
      for pi, p in enumerate(parts):
        t = getattr(p, "text", None)
        if isinstance(t, str) and t:
          out.append((ci, pi, p))
  except Exception:
    pass
  return out


def shrink_contents_text_inplace(contents: Any, max_total_chars: int) -> bool:
  """
  Reduce total payload size by trimming oldest text parts first, then truncating
  old tool payloads.

  Mutates `Part.text` and tool-response payloads in place. Returns True if
  anything changed.
  """
  if not contents or max_total_chars <= 0:
    return False
  changed = False
  min_keep = _MIN_TEXT_AFTER_SHRINK
  while True:
    total = estimate_contents_text_chars(contents)
    if total <= max_total_chars:
      return changed
    refs = _ordered_text_part_refs(contents)
    excess = total - max_total_chars
    progressed = False
    for _ci, _pi, p in refs:
      t = getattr(p, "text", None) or ""
      if not isinstance(t, str) or len(t) <= min_keep:
        continue
      room = len(t) - min_keep
      if room <= 0:
        continue
      trim = min(excess, room)
      if trim <= 0:
        continue
      # Hard cut only: appending a marker can grow the string when trim is small
      # and cause an infinite shrink loop.
      cut_len = len(t) - trim
      core = t[:cut_len]
      # Append marker only when it strictly shrinks vs the previous text (avoids a
      # no-op when the string already ended with the same marker).
      if (
          trim >= len(_CONTEXT_MARK)
          and cut_len >= min_keep
          and cut_len + len(_CONTEXT_MARK) < len(t)
      ):
        p.text = core + _CONTEXT_MARK
      else:
        p.text = core
      changed = True
      progressed = True
      break
    if progressed:
      continue

    # No more shrinkable visible text parts; try truncating old tool payloads.
    tool_refs = _ordered_tool_payload_part_refs(contents)
    for _ci, _pi, part, payload_dict in tool_refs:
      try:
        # Find the largest relevant string so we can choose an aggressive max_str.
        max_len = _largest_string_len_in_tool_result(payload_dict)
        if max_len <= _MIN_TOOL_AFTER_SHRINK:
          continue
        # Choose a max string size that should reduce at least some of the excess.
        if excess >= max_len:
          # Extreme overage: clearing oldest tool payload is closer to
          # microcompact behavior and guarantees progress.
          cleared = {"[Old tool result content cleared]": True}
          if getattr(part, "tool_response", None) is not None:
            part.tool_response.response = cleared
          else:
            part.function_response.response = cleared
          changed = True
          progressed = True
          break

        target_max_str = max(50, max_len - excess)
        if target_max_str >= max_len:
          continue
        new_d, did = truncate_tool_result_dict(payload_dict, int(target_max_str))
        if did:
          # Assign back to the correct payload holder.
          if getattr(part, "tool_response", None) is not None:
            part.tool_response.response = new_d
          else:
            part.function_response.response = new_d
          changed = True
          progressed = True
          break
      except Exception:
        continue

    if not progressed:
      return changed


def truncate_string(s: str, max_chars: int) -> str:
  """Hard truncate to `max_chars` without length growth."""
  if max_chars <= 0 or len(s) <= max_chars:
    return s
  if max_chars <= len(_TOOL_TRUNC_MARK):
    return s[:max_chars]
  cut = max_chars - len(_TOOL_TRUNC_MARK)
  return s[:cut] + _TOOL_TRUNC_MARK


def truncate_tool_result_dict(d: dict[str, Any], max_str: int) -> tuple[dict[str, Any], bool]:
  """
  Shallow-copy `d` and cap long string / list fields common in tool outputs.

  Returns (new_dict, changed).
  """
  if not isinstance(d, dict) or max_str <= 0:
    return d, False
  out: dict[str, Any] = dict(d)
  changed = False

  for key in (
      "stdout",
      "stderr",
      "content",
      "text",
      "output",
      "body",
      "message",
      "data",
  ):
    v = out.get(key)
    if isinstance(v, str) and len(v) > max_str:
      out[key] = truncate_string(v, max_str)
      changed = True

  for key in ("matches", "lines", "results"):
    v = out.get(key)
    if not isinstance(v, list) or not v:
      continue
    new_list: list[Any] = []
    cap = _MAX_MATCH_ITEMS
    for item in v[:cap]:
      if isinstance(item, str) and len(item) > max_str:
        new_list.append(truncate_string(item, max_str))
        changed = True
      elif isinstance(item, dict):
        sub, sub_ch = truncate_tool_result_dict(item, max_str)
        new_list.append(sub)
        changed = changed or sub_ch
      else:
        new_list.append(item)
    if len(v) > cap:
      new_list.append(f"… [{len(v) - cap} more items omitted]")
      changed = True
    if new_list != v:
      out[key] = new_list
      changed = True

  return out, changed


def _ordered_tool_payload_part_refs(contents: Any) -> list[tuple[int, int, Any, dict[str, Any]]]:
  """(content_idx, part_idx, part, payload_dict) for oldest-first tool payloads."""
  out: list[tuple[int, int, Any, dict[str, Any]]] = []
  try:
    for ci, c in enumerate(contents):
      parts = getattr(c, "parts", None) or []
      for pi, part in enumerate(parts):
        # tool_response is stored as ToolResponse(response=dict)
        tr = getattr(part, "tool_response", None)
        if tr is not None:
          resp = getattr(tr, "response", None)
          if isinstance(resp, dict) and resp:
            out.append((ci, pi, part, resp))
            continue

        fr = getattr(part, "function_response", None)
        if fr is not None:
          resp = getattr(fr, "response", None)
          if isinstance(resp, dict) and resp:
            out.append((ci, pi, part, resp))
  except Exception:
    pass
  return out


def _largest_string_len_in_tool_result(d: dict[str, Any]) -> int:
  """Best-effort maximum length of relevant string fields in a tool result."""
  max_len = 0
  try:
    for key in (
        "stdout",
        "stderr",
        "content",
        "text",
        "output",
        "body",
        "message",
        "data",
    ):
      v = d.get(key)
      if isinstance(v, str):
        max_len = max(max_len, len(v))

    for key in ("matches", "lines", "results"):
      v = d.get(key)
      if not isinstance(v, list):
        continue
      for item in v[:_MAX_MATCH_ITEMS]:
        if isinstance(item, str):
          max_len = max(max_len, len(item))
        elif isinstance(item, dict):
          # Only consider nested dicts with the same rough keys.
          max_len = max(max_len, _largest_string_len_in_tool_result(item))
  except Exception:
    return max_len
  return max_len


def make_before_model_context_shrink_callback(cfg: GemCodeConfig):
  """Soft ceiling on total text chars in `llm_request.contents` before the model runs."""
  if not getattr(cfg, "context_shrink_enabled", True):
    return None
  max_chars = getattr(cfg, "max_context_chars", None)
  if not isinstance(max_chars, int) or max_chars <= 0:
    return None

  async def before_model(callback_context, llm_request):
    try:
      contents = getattr(llm_request, "contents", None)
    except Exception:
      return None
    if not contents:
      return None
    if estimate_contents_text_chars(contents) <= max_chars:
      return None
    shrink_contents_text_inplace(contents, max_chars)
    return None

  return before_model
