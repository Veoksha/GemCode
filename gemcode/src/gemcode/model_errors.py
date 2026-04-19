"""User-facing formatting for Gemini / GenAI model failures."""

from __future__ import annotations

import re

# Seconds between retries for transient API failures (shared by invoke.py and TUI).
API_TRANSIENT_RETRY_DELAYS_SEC: tuple[float, ...] = (2.0, 5.0, 12.0)


def is_transient_error(error: Exception) -> bool:
  """Return True for HTTP 503 / 429 and similar transient API errors that are safe to retry.

  Transient means: the request was fine, the server was temporarily unavailable or
  rate-limited. Retrying the same request (with backoff) will likely succeed.
  """
  try:
    from google.genai import errors as genai_errors
    if isinstance(error, genai_errors.APIError):
      code = int(getattr(error, "code", None) or 0) or None
      if code in (429, 503):
        return True
      # Some 500-range server errors are also transient (502 Bad Gateway, etc.)
      if code is not None and 500 <= code < 600 and code not in (400, 401, 403, 404):
        return True
  except Exception:
    pass

  # gRPC / google-api-core equivalents
  et = type(error).__name__
  if "ResourceExhausted" in et or "ServiceUnavailable" in et or "DeadlineExceeded" in et:
    return True

  msg = str(error)
  ml = msg.lower()

  # httpx / google-genai often raise ``ServerError`` for HTTP 500 without ``APIError.code``.
  if et == "ServerError" or "ServerError" in et:
    if any(code in msg for code in ("500", "502", "503", "504")):
      return True

  # Gemini REST body shape: ``'code': 500`` / ``"INTERNAL"`` / "Internal error encountered."
  if ("'code': 500" in msg or '"code": 500' in msg or re.search(r"\b500\b", msg)) and any(
    p in ml for p in ("internal", "unavailable", "try again", "deadline", "timeout", "backend")
  ):
    return True
  if "internal error encountered" in ml:
    return True

  # Match the specific phrases Gemini uses in 503 responses
  if "503" in msg and any(p in msg for p in ("high demand", "service unavailable", "overloaded")):
    return True
  if "429" in msg and any(p in msg for p in ("rate limit", "quota", "resource exhausted")):
    return True
  return False


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
