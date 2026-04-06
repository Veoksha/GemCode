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
    Create and maintain a structured task list for the current session.
    Tracks progress, organises complex tasks, and makes multi-step work
    visible to the user.

    ## When to use
    Use proactively when:
    1. Task has 3 or more distinct steps
    2. Task is non-trivial and requires careful planning
    3. User provides a list of things to do (numbered or comma-separated)
    4. After receiving new instructions — capture them as todos immediately
    5. When you start working on a sub-task — mark it in_progress BEFORE beginning.
       Only one task should be in_progress at a time.
    6. After completing a sub-task — mark it completed and add any discovered follow-ups

    ## When NOT to use
    Skip this tool when:
    1. There is only one simple, straightforward task
    2. The task is trivial (can be done in 1-2 steps)
    3. The task is purely conversational or informational
    4. Answering a question that requires no planning

    ## Verification
    After completing a list of 3 or more tasks, if none of them was a verification
    step, add a final verification task: "Verify all changes are correct and
    consistent" — then actually do it (re-read key files, run tests, check imports).

    ## Args
    - merge: True = upsert by id (update specific items). False = replace entire list.
    - todos: list of {id: str, content: str, status: pending|in_progress|completed|cancelled}

    ## Examples
    Good use (complex multi-step task):
      todo_write(merge=False, todos=[
        {"id":"1","content":"Read current auth.ts","status":"in_progress"},
        {"id":"2","content":"Add JWT refresh logic","status":"pending"},
        {"id":"3","content":"Update tests","status":"pending"},
        {"id":"4","content":"Run npm run build to verify","status":"pending"},
      ])

    Bad use (single trivial task — just do it directly):
      todo_write(merge=False, todos=[{"id":"1","content":"Fix typo in README","status":"pending"}])
      # Don't do this. Just fix the typo.
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
