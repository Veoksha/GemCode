from __future__ import annotations

import os
from typing import Any

from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from gemcode.audit import append_audit
from gemcode.config import GemCodeConfig


async def run_background_learner(*, cfg: GemCodeConfig, callback_context: Any) -> None:
  """
  Bounded post-turn learning pass.

  It reads a small slice of the most recent events and suggests 0-3 durable facts
  to save to curated memory and/or notes. It uses a cheap model and is opt-in.
  """
  try:
    events = list(getattr(callback_context.session, "events", None) or [])
  except Exception:
    events = []

  # Build a compact transcript tail.
  tail = []
  for ev in events[-20:]:
    try:
      author = getattr(ev, "author", None) or ""
      if author == "user":
        who = "User"
      else:
        who = "GemCode"
      content = getattr(ev, "content", None)
      parts = getattr(content, "parts", None) or []
      texts = [getattr(p, "text", None) for p in parts if getattr(p, "text", None)]
      if not texts:
        continue
      t = "".join(texts).strip()
      if not t:
        continue
      tail.append(f"{who}: {t[:1200]}")
    except Exception:
      continue

  if not tail:
    return

  prompt = (
    "You are a background learner for GemCode.\n"
    "Extract ONLY durable, non-sensitive facts worth saving for future sessions.\n"
    "Return STRICT JSON only (no markdown), with this schema:\n"
    "{\n"
    '  \"facts\": [\n'
    "    {\"target\": \"memory\"|\"user\"|\"notes\", \"text\": \"...\"}\n"
    "  ]\n"
    "}\n"
    "Rules:\n"
    "- 0 to 3 facts maximum.\n"
    "- Never include secrets (API keys, tokens, passwords).\n"
    "- Prefer file paths, commands, conventions, user preferences.\n"
    "- If nothing worth saving, return {\"facts\": []}.\n\n"
    "Transcript tail:\n"
    + "\n".join(tail)
  )

  model = os.environ.get("GEMCODE_BACKGROUND_LEARNER_MODEL", "gemini-2.5-flash")
  llm = Gemini(model=model, use_interactions_api=False)
  req = LlmRequest(
    model=model,
    contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
    config=types.GenerateContentConfig(temperature=0.2),
  )

  out = ""
  async for resp in llm.generate_content_async(req, stream=False):
    try:
      if resp.content and resp.content.parts:
        texts = [getattr(p, "text", None) for p in resp.content.parts if getattr(p, "text", None)]
        if texts:
          out = "".join(texts).strip()
    except Exception:
      continue

  append_audit(cfg.project_root, {"phase": "background_learner", "ok": True, "raw": out[:4000]})

  # Parse and apply facts (best-effort).
  import json
  try:
    obj = json.loads(out) if out else {}
  except Exception:
    return
  facts = obj.get("facts") if isinstance(obj, dict) else None
  if not isinstance(facts, list):
    return

  from gemcode.curated_memory import append_fact
  from gemcode.tools.notes import build_notes_tools
  notes_tools = build_notes_tools(cfg.project_root)
  append_note = notes_tools[0]

  saved = []
  for f in facts[:3]:
    if not isinstance(f, dict):
      continue
    target = str(f.get("target") or "memory").strip().lower()
    text = str(f.get("text") or "").strip()
    if not text:
      continue
    if target in ("memory", "user"):
      res = append_fact(cfg.project_root, target=target, text=text)
      if "error" not in res:
        saved.append({"target": target, "text": text})
    elif target == "notes":
      res = append_note(f"- **Learned**: {text}")
      if isinstance(res, dict) and res.get("status") in ("appended", "already_exists"):
        saved.append({"target": "notes", "text": text})

  if saved:
    append_audit(cfg.project_root, {"phase": "background_learner_saved", "saved": saved})

