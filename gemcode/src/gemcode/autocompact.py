"""
interactive CLI–style autocompact for ADK/Gemini.

GemCode already has:
- bounded tool output (after_tool truncation)
- soft context shrink (before_model trimming/clearing)

This module adds:
- threshold-based autocompact: when context is near the ceiling, summarize older
  conversation into a compact "memory" message and keep only the tail turns.
"""

from __future__ import annotations

import os
from typing import Any

from google.genai import Client
from google.genai import types

from gemcode.config import GemCodeConfig
from gemcode.context_budget import estimate_contents_text_chars

_AC_STATE_KEY = "gemcode:autocompact"
_AC_FAILURES_KEY = "gemcode:autocompact_failures"
_AC_LAST_SUMMARY_KEY = "gemcode:autocompact_last_summary"


def _truthy_env(name: str, *, default: bool = False) -> bool:
  v = os.environ.get(name)
  if v is None:
    return default
  return v.lower() in ("1", "true", "yes", "on")


def _autocompact_enabled(cfg: GemCodeConfig) -> bool:
  # Default on to match "it knows what to do and when".
  if os.environ.get("GEMCODE_AUTOCOMPACT") is not None:
    return _truthy_env("GEMCODE_AUTOCOMPACT", default=True)
  return True


def _autocompact_threshold_chars(cfg: GemCodeConfig) -> int:
  # uses token windows; we use a character proxy budget since
  # Gemini tokenizers vary and ADK does not expose a cheap exact counter.
  max_chars = int(getattr(cfg, "max_context_chars", 0) or 0)
  if max_chars <= 0:
    return 0
  buffer_chars = int(os.environ.get("GEMCODE_AUTOCOMPACT_BUFFER_CHARS", "60000"))
  return max(50_000, max_chars - max(10_000, buffer_chars))


def _max_failures() -> int:
  return int(os.environ.get("GEMCODE_AUTOCOMPACT_MAX_FAILURES", "3"))


def _tail_keep_contents(cfg: GemCodeConfig) -> int:
  return int(os.environ.get("GEMCODE_AUTOCOMPACT_KEEP_CONTENT_ITEMS", "18"))


def _summary_model(cfg: GemCodeConfig) -> str:
  return os.environ.get("GEMCODE_AUTOCOMPACT_MODEL", getattr(cfg, "model", ""))


def _build_summary_prompt(contents: Any) -> str:
  # Safe, bounded textualization for summarization. We do not try to serialize
  # structured tool blocks fully; the pre-model context shrink already clears
  # most large payloads under pressure.
  lines: list[str] = []
  for c in contents or []:
    role = getattr(c, "role", "unknown")
    parts = getattr(c, "parts", None) or []
    texts: list[str] = []
    for p in parts:
      t = getattr(p, "text", None)
      if isinstance(t, str) and t.strip():
        texts.append(t.strip())
    if not texts:
      continue
    joined = "\n".join(texts)
    # Bound per content item to avoid PTL inside the compact call itself.
    if len(joined) > 20_000:
      joined = joined[:20_000] + "\n… [truncated for autocompact]"
    lines.append(f"{role.upper()}:\n{joined}")
  transcript = "\n\n".join(lines)
  if len(transcript) > 180_000:
    transcript = transcript[:180_000] + "\n… [older transcript truncated for autocompact]"

  return (
    "You are GemCode. Summarize the conversation so far into a compact, actionable memory.\n"
    "Requirements:\n"
    "- Preserve key decisions, constraints, and current plan.\n"
    "- Preserve important file paths, commands, and errors.\n"
    "- Keep it concise but information-dense.\n"
    "- Do NOT include tool call JSON; paraphrase.\n\n"
    "Conversation:\n"
    f"{transcript}\n"
  )


def _summarize_via_genai(cfg: GemCodeConfig, prompt: str) -> str:
  api_key = os.environ.get("GOOGLE_API_KEY")
  if not api_key:
    raise RuntimeError("GOOGLE_API_KEY not set (required for autocompact summary call)")
  client = Client(api_key=api_key)
  model = _summary_model(cfg) or getattr(cfg, "model", "")
  resp = client.models.generate_content(
    model=model,
    contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
    config=types.GenerateContentConfig(temperature=0.2),
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
    raise RuntimeError("autocompact summary call returned empty text")
  # Hard bound
  return text[:80_000]


def make_before_model_autocompact_callback(cfg: GemCodeConfig):
  if not _autocompact_enabled(cfg):
    return None

  async def before_model(callback_context, llm_request):
    try:
      contents = getattr(llm_request, "contents", None) or []
    except Exception:
      return None

    threshold = _autocompact_threshold_chars(cfg)
    if threshold <= 0:
      return None

    used = estimate_contents_text_chars(contents)
    force = os.environ.get("GEMCODE_AUTOCOMPACT_FORCE", "").lower() in (
      "1",
      "true",
      "yes",
      "on",
    )
    if not force and used < threshold:
      return None

    st = getattr(callback_context, "state", None) or {}
    failures = int(st.get(_AC_FAILURES_KEY, 0) or 0)
    if failures >= _max_failures():
      return None

    # Build summary from the "older" prefix; keep tail untouched.
    #
    # Keep a reasonable tail by default, but allow compaction even in short
    # conversations that become huge due to tool payloads.
    requested_keep = max(4, _tail_keep_contents(cfg))
    # Need at least 2 items in the summarize slice to be worth it:
    # [first] + [summary] + [tail...]
    max_keep_for_summarize = max(2, len(contents) - 2)
    keep_n = min(requested_keep, max_keep_for_summarize)
    keep_first = 1 if contents else 0
    tail = contents[-keep_n:] if len(contents) > keep_n else list(contents)
    prefix = []
    if keep_first:
      prefix = contents[:1]
      summarize_slice = contents[1:-keep_n] if len(contents) > (1 + keep_n) else []
    else:
      summarize_slice = contents[:-keep_n] if len(contents) > keep_n else []

    if not summarize_slice:
      return None

    try:
      prompt = _build_summary_prompt(summarize_slice)
      summary_text = _summarize_via_genai(cfg, prompt)
    except Exception:
      st[_AC_FAILURES_KEY] = failures + 1
      return None

    st[_AC_FAILURES_KEY] = 0
    st[_AC_LAST_SUMMARY_KEY] = summary_text
    st[_AC_STATE_KEY] = True

    summary_msg = types.Content(
      role="user",
      parts=[
        types.Part(
          text=(
            "Conversation summary (autocompacted):\n"
            f"{summary_text}\n"
          )
        )
      ],
    )

    llm_request.contents = [*prefix, summary_msg, *tail]
    # One-shot force flag.
    if force:
      os.environ.pop("GEMCODE_AUTOCOMPACT_FORCE", None)
    return None

  return before_model

