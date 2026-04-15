"""
Mitigate "system prompt contamination" in tool queries.

Inspired by MemPalace's query sanitizer. Goal: prevent catastrophic search degradation
when an agent accidentally prefixes a long system instruction to a short query.
"""

from __future__ import annotations

import re

MAX_QUERY_LENGTH = 250
SAFE_QUERY_LENGTH = 200
MIN_QUERY_LENGTH = 10

_SENTENCE_SPLIT = re.compile(r"[.!?。！？\n]+")
_QUESTION_MARK = re.compile(r'[?？]\s*["\']?\s*$')
_QUOTE_CHARS = {"'", '"'}


def sanitize_tool_query(raw_query: str) -> dict[str, object]:
  """
  Return a best-effort clean query.

  Returns dict:
    clean_query: str
    was_sanitized: bool
    method: str
  """
  if not raw_query or not str(raw_query).strip():
    return {"clean_query": "", "was_sanitized": False, "method": "empty"}

  raw_query = str(raw_query).strip()
  n = len(raw_query)

  def _strip_wrapping_quotes(s: str) -> str:
    s = (s or "").strip()
    while len(s) >= 2 and s[:1] in _QUOTE_CHARS and s[:1] == s[-1:]:
      s = s[1:-1].strip()
    if not s:
      return ""
    if s[:1] in _QUOTE_CHARS:
      s = s[1:].strip()
    if s[-1:] in _QUOTE_CHARS:
      s = s[:-1].strip()
    return s

  def _trim_candidate(s: str) -> str:
    s = _strip_wrapping_quotes(s)
    if len(s) <= MAX_QUERY_LENGTH:
      return s
    frags = [_strip_wrapping_quotes(x) for x in _SENTENCE_SPLIT.split(s) if x.strip()]
    for frag in reversed(frags):
      if MIN_QUERY_LENGTH <= len(frag) <= MAX_QUERY_LENGTH:
        return frag
    return s[-MAX_QUERY_LENGTH:].strip()

  if n <= SAFE_QUERY_LENGTH:
    return {"clean_query": raw_query, "was_sanitized": False, "method": "passthrough"}

  # Prefer last question-looking segment.
  segments = [s.strip() for s in raw_query.split("\n") if s.strip()]
  question_candidates: list[str] = []
  for seg in reversed(segments):
    if _QUESTION_MARK.search(seg):
      question_candidates.append(seg)
  if not question_candidates:
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(raw_query) if s.strip()]
    for sent in reversed(sentences):
      if "?" in sent or "？" in sent:
        question_candidates.append(sent)

  if question_candidates:
    cand = _trim_candidate(question_candidates[0])
    if len(cand) >= MIN_QUERY_LENGTH:
      return {"clean_query": cand, "was_sanitized": True, "method": "question_extraction"}

  # Otherwise take the last meaningful segment.
  for seg in reversed(segments):
    cand = _trim_candidate(seg)
    if len(cand) >= MIN_QUERY_LENGTH:
      return {"clean_query": cand, "was_sanitized": True, "method": "tail_sentence"}

  # Fallback: tail truncation.
  cand = raw_query[-MAX_QUERY_LENGTH:].strip()
  return {"clean_query": cand, "was_sanitized": True, "method": "tail_truncation"}

