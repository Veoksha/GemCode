"""User-facing formatting for Gemini / GenAI model failures."""

from __future__ import annotations

import re


def _sanitize_api_text(s: str) -> str:
  """Strip likely API key material from strings shown to the user."""
  if not s:
    return ""
  out = s
  out = re.sub(r"AIza[0-9A-Za-z_-]{20,}", "[REDACTED]", out)
  out = re.sub(r"ya29\.[0-9A-Za-z_-]+", "[REDACTED]", out)
  return out


def _hint_for_http(code: int | None, message: str) -> str:
  ml = (message or "").lower()
  parts: list[str] = []
  if code == 429:
    parts.append("Rate limited — wait a bit, retry, or switch model.")
  elif code in (401, 403):
    parts.append("Check GOOGLE_API_KEY and that the Generative Language API is enabled.")
  elif code == 404:
    parts.append("Model or endpoint not found — verify GEMCODE_MODEL matches an available model id.")
  elif code == 400:
    if "context" in ml or "token" in ml or "length" in ml:
      parts.append(
          "Request may be too large — try GEMCODE_MAX_CONTEXT_CHARS / "
          "GEMCODE_TOOL_RESULT_MAX_CHARS, start a new session (`--session`), or shorten history."
      )
    elif "api key" in ml or "permission" in ml:
      parts.append("Verify API key and project quotas.")
  elif code is not None and code >= 500:
    parts.append("Google API had a server error — retry shortly.")
  return " ".join(parts)


def format_model_error_for_user(error: Exception) -> str:
  """
  Build a short, actionable message. Avoids duplicating UI prefixes like 'GemCode:'.
  """
  try:
    from google.genai import errors as genai_errors
  except ImportError:
    genai_errors = None

  msg = _sanitize_api_text(str(error).strip())
  code: int | None = None
  api_message = ""

  if genai_errors is not None and isinstance(error, genai_errors.APIError):
    code = int(getattr(error, "code", None) or 0) or None
    api_message = _sanitize_api_text(
        str(getattr(error, "message", None) or getattr(error, "status", None) or "")
    )
    if not api_message and getattr(error, "details", None) is not None:
      d = error.details
      if isinstance(d, dict):
        api_message = _sanitize_api_text(str(d.get("message") or d)[:1200])
      else:
        api_message = _sanitize_api_text(str(d)[:1200])
    hint = _hint_for_http(code, api_message + msg)
    head = f"HTTP {code}" if code else "API error"
    body = f"{head}"
    if api_message:
      body += f": {api_message[:900]}"
    elif msg:
      body += f": {msg[:900]}"
    if hint:
      body += f" {hint}"
    return body[:2000]

  # google.api_core / grpc-style
  et = type(error).__name__
  if "ResourceExhausted" in et or "429" in msg:
    return (
        "Rate limited (429). Wait and retry, or use a lighter model. "
        f"{msg[:400]}"
    )[:2000]

  hint = _hint_for_http(None, msg)
  base = f"{et}: {msg[:1200]}" if msg else et
  if hint and hint not in base:
    base = f"{base} {hint}"
  return _sanitize_api_text(base)[:2000]
