"""
Durable fleet/agent reports for the *main* GemCode session.

Bus messages (org.report, job.report, agent.report) stream to subscribed UIs but do
not become ADK conversation turns. This module appends completed reports to
`.gemcode/fleet_reports.jsonl` at the fleet root; `run_turn` drains them into the
next user-visible prompt so the manager model sees outcomes without manual copy/paste.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_DIGEST_MARKER = "[GemCode: fleet digest]"

# Debounced background enqueue (enqueue / both modes).
_followup_timer: threading.Timer | None = None
_followup_timer_lock = threading.Lock()


def inject_enabled() -> bool:
  return os.environ.get("GEMCODE_FLEET_REPORTS_INJECT", "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
  )


def auto_continue_enabled() -> bool:
  return os.environ.get("GEMCODE_FLEET_REPORTS_AUTO_CONTINUE", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
  )


def auto_continue_mode() -> str:
  """`tui` (extra turns in the GemCode TUI or plain REPL), `enqueue` (runtime job), or `both`."""
  m = (os.environ.get("GEMCODE_FLEET_REPORTS_AUTO_CONTINUE_MODE") or "tui").strip().lower()
  if m in ("enqueue", "both"):
    return m
  return "tui"


def max_auto_chain() -> int:
  try:
    return max(0, min(10, int(os.environ.get("GEMCODE_FLEET_REPORTS_AUTO_CONTINUE_MAX", "3"))))
  except Exception:
    return 3


def fleet_digest_prompt() -> str:
  return (
    f"{_DIGEST_MARKER}\n"
    "Background agents may have completed work; fleet reports were drained at the start of this turn if any were queued.\n"
    "Summarize outcomes for the user in short bullets. Do not call org_delegate, org_spawn, or spawn_subtasks "
    "unless you must fix a reported error."
  )


def is_fleet_digest_prompt(text: str) -> bool:
  return (text or "").lstrip().startswith(_DIGEST_MARKER)


def has_pending_fleet_reports(project_root: Path) -> bool:
  if not inject_enabled():
    return False
  try:
    from gemcode.org import resolve_fleet_root

    fleet_root = resolve_fleet_root(project_root)
  except Exception:
    fleet_root = project_root
  p = _fleet_reports_path(fleet_root)
  try:
    if not p.is_file():
      return False
    return bool(p.read_text(encoding="utf-8", errors="replace").strip())
  except Exception:
    return False


def prepend_drain_to_prompt(project_root: Path, prompt: str) -> str:
  """Drain fleet inbox into the user message (for code paths that do not call ``run_turn``)."""
  preamble = drain_for_prompt(project_root)
  if not preamble:
    return prompt or ""
  return preamble + "\n\n---\n\n" + (prompt or "")


def _enqueue_digest_job_sync(fleet_root: Path) -> None:
  if not has_pending_fleet_reports(fleet_root):
    return
  sock = os.environ.get("GEMCODE_KAIRA_SOCKET") or str(fleet_root / ".gemcode" / "ipc.sock")
  if not Path(sock).is_file():
    return
  digest = (
    "[GEMCODE_FLEET_DIGEST=1] Fleet inbox has pending reports. Summarize them briefly for the human (bullets). "
    "Do not call org_delegate, org_spawn, or spawn_subtasks unless fixing an explicit failure."
  )
  try:
    import asyncio

    async def _run() -> None:
      from gemcode.kaira_client import KairaIpcClient

      c = await KairaIpcClient.connect(socket_path=sock)
      try:
        await c.request(
          action="enqueue",
          prompt=digest,
          priority=-3,
          session_id="",
          meta={"gemcode": {"fleet_digest": True}},
        )
      finally:
        await c.close()

    asyncio.run(_run())
  except Exception:
    return


def schedule_enqueue_digest(fleet_root: Path) -> None:
  """After new reports land, optionally enqueue one debounced manager digest job on the runtime."""
  if not inject_enabled() or not auto_continue_enabled():
    return
  if auto_continue_mode() not in ("enqueue", "both"):
    return

  def _fire() -> None:
    try:
      _enqueue_digest_job_sync(fleet_root)
    except Exception:
      pass

  global _followup_timer
  delay = 0.45
  try:
    delay = float(os.environ.get("GEMCODE_FLEET_REPORTS_ENQUEUE_DEBOUNCE_S", "0.45"))
  except Exception:
    pass
  with _followup_timer_lock:
    if _followup_timer is not None:
      try:
        _followup_timer.cancel()
      except Exception:
        pass
    _followup_timer = threading.Timer(delay, _fire)
    _followup_timer.daemon = True
    _followup_timer.start()


def _fleet_reports_path(fleet_root: Path) -> Path:
  d = fleet_root / ".gemcode"
  d.mkdir(parents=True, exist_ok=True)
  return d / "fleet_reports.jsonl"


def _should_inbox_org_status(status: str) -> bool:
  s = (status or "").strip().lower()
  return s in ("finished", "failed")


def _should_inbox_agent_status(status: str) -> bool:
  s = (status or "").strip().lower()
  return s in ("finished", "failed")


def append_fleet_report(fleet_root: Path, *, topic: str, payload: dict[str, Any]) -> None:
  """Append one JSON line; failures are swallowed (reporting must not break workers)."""
  if not inject_enabled():
    return
  try:
    rec = {"ts_ms": int(time.time() * 1000), "topic": topic, "payload": payload}
    p = _fleet_reports_path(fleet_root)
    line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
    with open(p, "a", encoding="utf-8") as f:
      f.write(line)
      f.flush()
    schedule_enqueue_digest(fleet_root)
  except Exception:
    return


def maybe_append_org_report(fleet_root: Path, payload: dict[str, Any]) -> None:
  st = str(payload.get("status") or "")
  if not _should_inbox_org_status(st):
    return
  append_fleet_report(fleet_root, topic="org.report", payload=payload)


def maybe_append_job_report(fleet_root: Path, payload: dict[str, Any]) -> None:
  st = str(payload.get("status") or "").strip().lower()
  if st not in ("finished", "failed"):
    return
  append_fleet_report(fleet_root, topic="job.report", payload=payload)


def maybe_append_agent_report(fleet_root: Path, payload: dict[str, Any]) -> None:
  st = str(payload.get("status") or "")
  if not _should_inbox_agent_status(st):
    return
  append_fleet_report(fleet_root, topic="agent.report", payload=payload)


def _format_record(rec: dict[str, Any]) -> str:
  topic = str(rec.get("topic") or "")
  payload = rec.get("payload")
  if not isinstance(payload, dict):
    return ""
  lines: list[str] = []

  if topic == "org.report":
    st = str(payload.get("status") or "")
    mem = payload.get("member") if isinstance(payload.get("member"), dict) else {}
    name = str(mem.get("name") or mem.get("address") or "member")
    jid = str(payload.get("job_id") or "")
    task = str(payload.get("task") or "").strip()[:900]
    err = str(payload.get("error") or "").strip()
    lines.append(f"[org.report] {name} status={st} job_id={jid}")
    if task:
      lines.append(f"  task: {task}")
    if err:
      lines.append(f"  error: {err[:4000]}")
    res = payload.get("result")
    if isinstance(res, dict):
      rpt = res.get("report")
      if isinstance(rpt, str) and rpt.strip():
        lines.append(f"  report: {rpt[:8000]}")
      prev = res.get("preview")
      if isinstance(prev, str) and prev.strip() and not rpt:
        lines.append(f"  preview: {prev[:6000]}")
    elif isinstance(res, str) and res.strip():
      lines.append(f"  result: {res[:8000]}")

  elif topic == "job.report":
    lines.append(
      f"[job.report] job_id={payload.get('job_id')} status={payload.get('status')} "
      f"session_id={payload.get('session_id')}"
    )
    rpt = str(payload.get("report") or "").strip()
    if rpt:
      lines.append(f"  report: {rpt[:8000]}")

  elif topic == "agent.report":
    st = str(payload.get("status") or "")
    lines.append(
      f"[agent.report] status={st} sub_session_id={payload.get('sub_session_id')}"
    )
    task = str(payload.get("task") or "").strip()[:600]
    if task:
      lines.append(f"  task: {task}")
    err = str(payload.get("error") or "").strip()
    if err:
      lines.append(f"  error: {err[:4000]}")
    res = payload.get("result")
    if isinstance(res, dict):
      inner = res.get("result")
      if isinstance(inner, str) and inner.strip():
        lines.append(f"  result: {inner[:8000]}")
      elif res.get("ref"):
        lines.append(f"  result: (offloaded ref={res.get('ref')})")
    elif isinstance(res, str) and res.strip():
      lines.append(f"  result: {res[:8000]}")

  return "\n".join(lines)


def drain_for_prompt(project_root: Path, *, max_chars: int | None = None) -> str:
  """
  Read and clear `.gemcode/fleet_reports.jsonl` at the fleet root; return text to
  prepend to the next user turn.
  """
  if not inject_enabled():
    return ""
  if max_chars is None:
    try:
      max_chars = int(os.environ.get("GEMCODE_FLEET_REPORTS_MAX_CHARS", "14000"))
    except Exception:
      max_chars = 14_000
  try:
    from gemcode.org import resolve_fleet_root

    fleet_root = resolve_fleet_root(project_root)
  except Exception:
    fleet_root = project_root
  p = _fleet_reports_path(fleet_root)
  if not p.is_file():
    return ""
  try:
    raw = p.read_text(encoding="utf-8", errors="replace")
  except Exception:
    return ""
  if not raw.strip():
    return ""

  lines_in = [ln.strip() for ln in raw.splitlines() if ln.strip()]
  blocks: list[str] = []
  total = 0
  truncated = False
  resume_from = 0
  for i, line in enumerate(lines_in):
    try:
      rec = json.loads(line)
    except Exception:
      continue
    if not isinstance(rec, dict):
      continue
    b = _format_record(rec)
    if not b:
      continue
    need = len(b) + 2
    if total + need > max_chars:
      truncated = True
      resume_from = i
      break
    blocks.append(b)
    total += need
  else:
    resume_from = len(lines_in)

  remaining = lines_in[resume_from:] if truncated else []
  try:
    p.write_text("\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8")
  except Exception:
    pass

  if not blocks:
    return ""
  header = (
    "Fleet / agent reports (background completions — incorporate if still relevant; "
    "the user may not have typed a new message yet):\n\n"
  )
  body = "\n\n".join(blocks)
  if truncated:
    body += (
      "\n\n… (older fleet reports still queued in `.gemcode/fleet_reports.jsonl`; "
      "increase GEMCODE_FLEET_REPORTS_MAX_CHARS to drain more per turn)"
    )
  return header + body
