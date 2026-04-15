from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from gemcode.paths import PathEscapeError, resolve_under_allowed_roots
from gemcode.wal import append_wal_event


_OUTER_FENCE_REGEX = re.compile(r"\A\s*(`{3,}|~{3,})[^\n]*\n(.*)\n\1\s*\Z", re.DOTALL)
_URL_REGEX = re.compile(r"https?://[^\s)]+")
_FENCE_OPEN_REGEX = re.compile(r"^(\s{0,3})(`{3,}|~{3,})(.*)$")
_HEADING_REGEX = re.compile(r"^(#{1,6})\s+(.*)", re.MULTILINE)

# Files and paths that almost certainly contain secrets/PII. This tool sends file content
# to the Gemini API; refuse high-risk targets to avoid accidental exfiltration.
_SENSITIVE_BASENAME_REGEX = re.compile(
  r"(?ix)^("
  r"\.env(\..+)?"
  r"|\.netrc"
  r"|credentials(\..+)?"
  r"|secrets?(\..+)?"
  r"|passwords?(\..+)?"
  r"|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?"
  r"|authorized_keys"
  r"|known_hosts"
  r"|.*\.(pem|key|p12|pfx|crt|cer|jks|keystore|asc|gpg)"
  r")$"
)
_SENSITIVE_PATH_COMPONENTS = frozenset({".ssh", ".aws", ".gnupg", ".kube", ".docker"})
_SENSITIVE_NAME_TOKENS = (
  "secret",
  "credential",
  "password",
  "passwd",
  "apikey",
  "accesskey",
  "token",
  "privatekey",
)


def _is_sensitive_path(filepath: Path) -> bool:
  name = filepath.name
  if _SENSITIVE_BASENAME_REGEX.match(name):
    return True
  lowered_parts = {p.lower() for p in filepath.parts}
  if lowered_parts & _SENSITIVE_PATH_COMPONENTS:
    return True
  lower = re.sub(r"[_\-\s.]", "", name.lower())
  return any(tok in lower for tok in _SENSITIVE_NAME_TOKENS)


def _strip_llm_wrapper(text: str) -> str:
  """Strip a single outer ```markdown ...``` fence if it wraps the entire output."""
  m = _OUTER_FENCE_REGEX.match(text)
  if m:
    return m.group(2)
  return text


def _extract_urls(text: str) -> set[str]:
  return set(_URL_REGEX.findall(text or ""))


def _extract_headings(text: str) -> list[tuple[str, str]]:
  return [(level, title.strip()) for level, title in _HEADING_REGEX.findall(text or "")]


def _extract_code_blocks(text: str) -> list[str]:
  """Line-based fenced code block extractor supporting variable fence lengths."""
  blocks: list[str] = []
  lines = (text or "").split("\n")
  i = 0
  n = len(lines)
  while i < n:
    m = _FENCE_OPEN_REGEX.match(lines[i])
    if not m:
      i += 1
      continue
    fence_char = m.group(2)[0]
    fence_len = len(m.group(2))
    block_lines = [lines[i]]
    i += 1
    closed = False
    while i < n:
      close_m = _FENCE_OPEN_REGEX.match(lines[i])
      if (
        close_m
        and close_m.group(2)[0] == fence_char
        and len(close_m.group(2)) >= fence_len
        and close_m.group(3).strip() == ""
      ):
        block_lines.append(lines[i])
        closed = True
        i += 1
        break
      block_lines.append(lines[i])
      i += 1
    if closed:
      blocks.append("\n".join(block_lines))
  return blocks


@dataclass
class _ValidationResult:
  is_valid: bool
  errors: list[str]
  warnings: list[str]


def _validate_markdown(original: str, compressed: str) -> _ValidationResult:
  errors: list[str] = []
  warnings: list[str] = []

  # Headings: keep count and order.
  h1 = _extract_headings(original)
  h2 = _extract_headings(compressed)
  if len(h1) != len(h2):
    errors.append(f"Heading count mismatch: {len(h1)} vs {len(h2)}")
  if h1 != h2:
    warnings.append("Heading text/order changed")

  # Code blocks and URLs are strict invariants.
  if _extract_code_blocks(original) != _extract_code_blocks(compressed):
    errors.append("Code blocks not preserved exactly")

  u1 = _extract_urls(original)
  u2 = _extract_urls(compressed)
  if u1 != u2:
    errors.append(f"URL mismatch: lost={sorted(u1 - u2)[:6]}, added={sorted(u2 - u1)[:6]}")

  return _ValidationResult(is_valid=(not errors), errors=errors, warnings=warnings)


def _looks_like_natural_language(path: Path) -> bool:
  # Conservative: compress only markdown-ish inputs.
  ext = path.suffix.lower()
  if ext in (".md", ".markdown", ".txt", ".rst"):
    return True
  # Allow extensionless, but only under .gemcode/ by convention.
  if not ext and ".gemcode" in {p.lower() for p in path.parts}:
    return True
  return False


def _build_prompt(original: str, *, mode: str) -> str:
  style = {
    "lite": "Lite: remove filler/hedging, keep full sentences.",
    "full": "Full: drop articles, fragments ok, short synonyms.",
    "ultra": "Ultra: telegraphic, abbreviate, arrows for causality.",
  }.get(mode, "Full: drop articles, fragments ok, short synonyms.")

  return f"""
Compress this markdown into a terse style (caveman-like).

TARGET STYLE: {style}

STRICT RULES:
- Do NOT modify anything inside fenced code blocks (``` or ~~~). Copy them EXACTLY.
- Do NOT modify anything inside inline backticks. Copy EXACTLY.
- Preserve ALL URLs exactly.
- Preserve ALL headings exactly (same heading lines, same order).
- Return ONLY the compressed markdown body (no outer ```markdown fence).

Only compress natural language prose outside code/backticks.

TEXT:
{original}
""".strip()


def _build_fix_prompt(original: str, compressed: str, errors: list[str]) -> str:
  errors_str = "\n".join(f"- {e}" for e in errors)
  return f"""You are fixing a compressed markdown file. Specific validation errors were found.

CRITICAL RULES:
- DO NOT recompress or rephrase the whole file
- ONLY fix the listed errors — leave everything else exactly as-is
- The ORIGINAL is reference only (to restore missing content)
- Preserve the terse style in untouched sections

ERRORS TO FIX:
{errors_str}

ORIGINAL (reference only):
{original}

COMPRESSED (fix this):
{compressed}

Return ONLY the fixed compressed file. No explanation.
"""


def make_compress_memory_tool(cfg):
  """
  Build a function tool that compresses markdown-like memory files.
  """
  project_root = cfg.project_root

  def compress_memory_file(
    path: str,
    *,
    mode: str = "full",
    max_bytes: int = 500_000,
    backup_ext: str = ".original.md",
  ) -> dict:
    """
    Compress a markdown-like memory file to reduce input tokens.

    Safety:
    - Refuses paths that look like secrets (keys, credentials, .ssh, .aws, etc.)
    - Size-capped before any model call
    - Writes backup as <stem>{backup_ext}; aborts if backup exists
    - Validates headings/URLs/code blocks; restores original on failure
    """
    extra_roots = getattr(cfg, "_added_dirs", None) or {}
    try:
      p, _scope = resolve_under_allowed_roots(project_root, path, extra_roots=extra_roots)
    except PathEscapeError as e:
      return {"ok": False, "error": str(e)}

    if not p.exists():
      return {"ok": False, "error": f"File not found: {p}"}
    if not p.is_file():
      return {"ok": False, "error": f"Not a file: {p}"}
    if p.stat().st_size > max_bytes:
      return {"ok": False, "error": f"File too large (max {max_bytes} bytes): {p}"}
    if _is_sensitive_path(p):
      return {
        "ok": False,
        "error": (
          f"Refusing to compress {p}: filename/path looks sensitive. "
          "This tool sends file content to the Gemini API."
        ),
      }
    if not _looks_like_natural_language(p):
      return {"ok": False, "error": f"Refusing: not a markdown-like file: {p.name}"}
    if p.name.endswith(backup_ext):
      return {"ok": False, "error": f"Refusing: backup file target: {p.name}"}

    original = p.read_text(encoding="utf-8", errors="replace")
    backup_path = p.with_name(p.stem + backup_ext)
    if backup_path.exists():
      return {"ok": False, "error": f"Backup already exists: {backup_path}"}

    # Call Gemini (local-first but does cross the API boundary).
    try:
      from google.genai import Client
    except Exception as e:
      return {"ok": False, "error": f"google-genai unavailable: {e}"}

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
      return {"ok": False, "error": "GOOGLE_API_KEY is not set"}

    model = os.environ.get("GEMCODE_COMPRESS_MODEL") or getattr(cfg, "model_alt", None) or cfg.model

    client = Client(api_key=api_key)
    prompt = _build_prompt(original, mode=mode)
    try:
      resp = client.models.generate_content(
        model=model,
        contents=prompt,
      )
      text = getattr(resp, "text", None) or ""
    except Exception as e:
      return {"ok": False, "error": f"Gemini call failed: {type(e).__name__}: {e}"}

    compressed = _strip_llm_wrapper((text or "").strip())

    # Write backup + compressed, then validate with repair loop.
    backup_path.write_text(original, encoding="utf-8")
    p.write_text(compressed, encoding="utf-8")

    max_retries = 2
    for attempt in range(max_retries + 1):
      res = _validate_markdown(original, compressed)
      if res.is_valid:
        # Best-effort WAL: metadata only (no content).
        append_wal_event(
          project_root,
          type="compress_memory_file",
          data={
            "path": str(p),
            "backup_path": str(backup_path),
            "mode": mode,
            "chars_before": len(original),
            "chars_after": len(compressed),
            "warnings": res.warnings,
          },
        )
        return {
          "ok": True,
          "path": str(p),
          "backup_path": str(backup_path),
          "warnings": res.warnings,
          "chars_before": len(original),
          "chars_after": len(compressed),
        }
      if attempt >= max_retries:
        # Restore original.
        p.write_text(original, encoding="utf-8")
        try:
          backup_path.unlink()
        except OSError:
          pass
        return {"ok": False, "error": f"Validation failed: {res.errors}", "warnings": res.warnings}

      # Targeted fix
      fix_prompt = _build_fix_prompt(original, compressed, res.errors)
      try:
        resp2 = client.models.generate_content(model=model, contents=fix_prompt)
        fixed = _strip_llm_wrapper(((getattr(resp2, "text", None) or "")).strip())
      except Exception as e:
        p.write_text(original, encoding="utf-8")
        try:
          backup_path.unlink()
        except OSError:
          pass
        return {"ok": False, "error": f"Fix attempt failed: {type(e).__name__}: {e}"}

      compressed = fixed
      p.write_text(compressed, encoding="utf-8")

    return {"ok": False, "error": "Unexpected failure"}

  compress_memory_file.__name__ = "compress_memory_file"
  return compress_memory_file

