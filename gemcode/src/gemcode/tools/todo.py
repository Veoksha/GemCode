"""Session-scoped task list (Claude Code TodoWrite–style planning)."""

from __future__ import annotations

from typing import Any

from google.adk.tools.tool_context import ToolContext

from gemcode.config import GemCodeConfig

TODO_STATE_KEY = "gemcode:todos"
_STATUSES = frozenset({"pending", "in_progress", "completed", "cancelled"})


def make_todo_tool(cfg: GemCodeConfig):
  _ = cfg

  def todo_write(
    merge: bool,
    todos: list[dict[str, Any]],
    tool_context: ToolContext,
  ) -> dict[str, Any]:
    """
    Maintain a task list for this session. Use for multi-step work: plan, then
    mark items completed as you go.

    Args:
      merge: If true, upsert by task id and keep prior order for existing ids;
        if false, replace the entire list.
      todos: Each item must have id (str), content (str), and status
        (pending | in_progress | completed | cancelled).
    """
    if not isinstance(todos, list):
      return {"error": "todos must be a list"}
    if len(todos) > 32:
      return {"error": "At most 32 todos per call"}
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, raw in enumerate(todos):
      if not isinstance(raw, dict):
        return {"error": f"todos[{i}] must be an object"}
      tid = raw.get("id")
      content = raw.get("content", "")
      status = raw.get("status", "pending")
      if not isinstance(tid, str) or not tid.strip():
        return {"error": f"todos[{i}].id must be a non-empty string"}
      tid = tid.strip()
      if len(tid) > 200:
        return {"error": f"todos[{i}].id is too long"}
      if tid in seen:
        return {"error": f"duplicate id {tid!r}"}
      seen.add(tid)
      if not isinstance(content, str):
        content = str(content)
      if len(content) > 2000:
        content = content[:1997] + "..."
      if not isinstance(status, str) or status not in _STATUSES:
        return {
            "error": (
                f"todos[{i}].status must be one of "
                f"{', '.join(sorted(_STATUSES))}"
            ),
        }
      normalized.append({"id": tid, "content": content, "status": status})

    try:
      st = tool_context.state
    except Exception:
      return {"error": "Session state unavailable"}

    if not merge:
      st[TODO_STATE_KEY] = normalized
    else:
      prev = list(st.get(TODO_STATE_KEY, []) or [])
      by_id: dict[str, dict[str, Any]] = {}
      for x in prev:
        if isinstance(x, dict) and isinstance(x.get("id"), str):
          by_id[x["id"]] = dict(x)
      for item in normalized:
        by_id[item["id"]] = item
      prev_ids = [x["id"] for x in prev if isinstance(x, dict) and x.get("id")]
      out: list[dict[str, Any]] = []
      used: set[str] = set()
      for pid in prev_ids:
        if pid in by_id and pid not in used:
          out.append(by_id[pid])
          used.add(pid)
      for item in normalized:
        if item["id"] not in used:
          out.append(by_id[item["id"]])
          used.add(item["id"])
      st[TODO_STATE_KEY] = out

    return {"ok": True, "todos": st.get(TODO_STATE_KEY, []), "merge": merge}

  return todo_write
