from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from google.genai import Client
from google.genai import types


def _session_summary_dir(project_root: Path) -> Path:
  return project_root / ".gemcode" / "session-summaries"


def _session_summary_path(project_root: Path, session_id: str) -> Path:
  safe_id = (session_id or "unknown").strip().replace("/", "_")
  return _session_summary_dir(project_root) / f"{safe_id}.md"


def _load_session_transcript(project_root: Path, session_id: str, *, max_events: int = 120) -> list[str]:
  db = project_root / ".gemcode" / "sessions.sqlite"
  if not db.is_file():
    return []

  con = sqlite3.connect(str(db), timeout=5)
  cur = con.cursor()
  cur.execute(
    """
    SELECT event_data
    FROM events
    WHERE session_id=?
    ORDER BY timestamp ASC
    LIMIT ?
    """,
    (session_id, int(max_events)),
  )
  rows = cur.fetchall()
  con.close()

  lines: list[str] = []
  for (raw,) in rows:
    try:
      event = json.loads(raw)
    except Exception:
      continue
    if not isinstance(event, dict):
      continue

    author = str(event.get("author") or "").strip().lower()
    content = event.get("content") if isinstance(event.get("content"), dict) else {}
    parts = content.get("parts") if isinstance(content.get("parts"), list) else []
    texts: list[str] = []
    for p in parts:
      if isinstance(p, dict):
        t = p.get("text")
        if isinstance(t, str) and t.strip():
          texts.append(t.strip())
    if not texts:
      continue

    joined = "\n".join(texts)
    if len(joined) > 4000:
      joined = joined[:4000].rstrip() + "\n… [truncated]"

    who = "User" if author == "user" else "GemCode"
    lines.append(f"{who}: {joined}")
  return lines


def _build_prompt(transcript_lines: list[str], *, focus: str = "") -> str:
  transcript = "\n\n".join(transcript_lines)
  if len(transcript) > 120_000:
    transcript = transcript[:120_000] + "\n\n… [older transcript truncated]"

  focus_line = f"- Extra focus: {focus}\n" if focus.strip() else ""
  return (
    "You are a session summariser for GemCode.\n"
    "Summarise the session into compact, reusable memory for future runs.\n"
    "Return STRICT JSON only with this schema:\n"
    "{\n"
    '  "title": "short title",\n'
    '  "summary_markdown": "markdown summary",\n'
    '  "memory_facts": ["durable project facts"],\n'
    '  "user_facts": ["durable user preferences"],\n'
    '  "notes_markdown": "compact markdown note for .gemcode/notes.md",\n'
    '  "open_items": ["open tasks or blockers"]\n'
    "}\n"
    "Rules:\n"
    "- Keep summary_markdown concise but high-signal.\n"
    "- Preserve decisions, file paths, commands, errors, fixes, and next steps.\n"
    "- memory_facts/user_facts: 0 to 5 each, only durable non-sensitive facts.\n"
    "- notes_markdown should be compact and useful for the next session.\n"
    "- Never include secrets, API keys, passwords, or tokens.\n"
    f"{focus_line}"
    "\nTranscript:\n"
    f"{transcript}\n"
  )


def _extract_json_object(text: str) -> dict[str, Any] | None:
  """
  Best-effort parse:
  - strict JSON object
  - JSON object embedded in extra text (common model behavior)
  """
  raw = (text or "").strip()
  if not raw:
    return None
  try:
    obj = json.loads(raw)
    return obj if isinstance(obj, dict) else None
  except Exception:
    pass

  # Try to recover embedded JSON: find the largest {...} span.
  start = raw.find("{")
  end = raw.rfind("}")
  if start == -1 or end == -1 or end <= start:
    return None
  candidate = raw[start : end + 1].strip()
  try:
    obj = json.loads(candidate)
    return obj if isinstance(obj, dict) else None
  except Exception:
    return None


def _call_summary_model(*, model: str, prompt: str) -> tuple[dict[str, Any] | None, str]:
  api_key = os.environ.get("GOOGLE_API_KEY")
  if not api_key:
    raise RuntimeError("GOOGLE_API_KEY not set")

  client = Client(api_key=api_key)
  cfg = types.GenerateContentConfig(temperature=0.2)
  # Prefer structured JSON output when supported by the SDK/model.
  try:
    setattr(cfg, "response_mime_type", "application/json")
  except Exception:
    pass

  resp = client.models.generate_content(
      model=model,
      contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
      config=cfg,
  )

  out_parts: list[str] = []
  try:
    if resp.candidates:
      c0 = resp.candidates[0]
      content = getattr(c0, "content", None)
      for p in getattr(content, "parts", None) or []:
        t = getattr(p, "text", None)
        if isinstance(t, str) and t:
          out_parts.append(t)
  except Exception:
    pass

  text = "".join(out_parts).strip()
  if not text:
    raise RuntimeError("session summariser returned empty text")
  return _extract_json_object(text), text


def summarise_session(
  project_root: Path,
  *,
  session_id: str,
  model: str,
  focus: str = "",
) -> dict[str, Any]:
  transcript_lines = _load_session_transcript(project_root, session_id)
  if not transcript_lines:
    return {"error": "session transcript is empty", "session_id": session_id}

  prompt = _build_prompt(transcript_lines, focus=focus)
  data, raw_text = _call_summary_model(model=model, prompt=prompt)
  if data is None:
    # Graceful fallback: save raw text as summary_markdown so /summarise never hard-fails
    # due to JSON formatting drift.
    data = {
      "title": f"Session {session_id[:8]}",
      "summary_markdown": raw_text[:20_000],
      "memory_facts": [],
      "user_facts": [],
      "notes_markdown": "",
      "open_items": [],
    }

  title = str(data.get("title") or f"Session {session_id[:8]}").strip()[:120]
  summary_markdown = str(data.get("summary_markdown") or "").strip()
  notes_markdown = str(data.get("notes_markdown") or "").strip()
  memory_facts = [str(x).strip() for x in (data.get("memory_facts") or []) if str(x).strip()][:5]
  user_facts = [str(x).strip() for x in (data.get("user_facts") or []) if str(x).strip()][:5]
  open_items = [str(x).strip() for x in (data.get("open_items") or []) if str(x).strip()][:10]

  out_path = _session_summary_path(project_root, session_id)
  out_path.parent.mkdir(parents=True, exist_ok=True)
  ts = datetime.now().strftime("%Y-%m-%d %H:%M")

  body_parts = [
    f"# {title}",
    "",
    f"- session_id: `{session_id}`",
    f"- generated_at: {ts}",
  ]
  if focus.strip():
    body_parts.append(f"- focus: {focus}")
  body_parts.extend(["", "## Summary", summary_markdown or "- (empty)"])

  if open_items:
    body_parts.extend(["", "## Open items", *[f"- {x}" for x in open_items]])
  if memory_facts:
    body_parts.extend(["", "## Durable project facts", *[f"- {x}" for x in memory_facts]])
  if user_facts:
    body_parts.extend(["", "## Durable user facts", *[f"- {x}" for x in user_facts]])

  out_path.write_text("\n".join(body_parts).rstrip() + "\n", encoding="utf-8")

  saved_memory: list[str] = []
  saved_user: list[str] = []
  try:
    from gemcode.curated_memory import append_fact
    for fact in memory_facts:
      res = append_fact(project_root, target="memory", text=fact)
      if "error" not in res:
        saved_memory.append(fact)
    for fact in user_facts:
      res = append_fact(project_root, target="user", text=fact)
      if "error" not in res:
        saved_user.append(fact)
  except Exception:
    pass

  note_status: str | None = None
  if notes_markdown:
    try:
      from gemcode.tools.notes import build_notes_tools
      append_note, _read_note = build_notes_tools(project_root)
      note_text = (
        f"## Session summary — {title}\n"
        f"- Session: `{session_id}`\n"
        f"- Summary file: `{out_path}`\n\n"
        f"{notes_markdown}"
      )
      res = append_note(note_text)
      if isinstance(res, dict):
        note_status = str(res.get("status") or "")
    except Exception:
      note_status = None

  return {
    "ok": True,
    "session_id": session_id,
    "summary_path": str(out_path),
    "title": title,
    "summary_markdown": summary_markdown,
    "used_json": bool(data is not None and _extract_json_object(raw_text) is not None),
    "memory_facts_saved": saved_memory,
    "user_facts_saved": saved_user,
    "notes_status": note_status,
    "open_items": open_items,
  }

