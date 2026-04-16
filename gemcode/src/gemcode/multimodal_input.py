"""
Build multimodal user Content (text + inline files) for Gemini.

Paths may be absolute or relative to the current working directory, then project root.
MIME types are inferred from the filename (``mimetypes``) and optionally from file
headers (PDF, common images, some audio/video) so PDFs and other Gemini-supported
types work—not only images.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Sequence

from google.genai import types

_MAX_ATTACHMENTS = 16


def _max_attachment_bytes() -> int:
  raw = os.environ.get("GEMCODE_MAX_ATTACHMENT_BYTES")
  if raw:
    try:
      v = int(raw, 10)
      if v > 0:
        return v
    except ValueError:
      pass
  return 20 * 1024 * 1024


def resolve_attachment_path(p: Path | str, *, project_root: Path) -> Path:
  path = Path(p).expanduser()
  if path.is_absolute():
    return path.resolve()
  cwd_try = (Path.cwd() / path).resolve()
  if cwd_try.is_file():
    return cwd_try
  root_try = (project_root / path).resolve()
  if root_try.is_file():
    return root_try
  return (Path.cwd() / path).resolve()


# Backward-compatible name from the images-only era.
resolve_image_path = resolve_attachment_path


def _sniff_mime(head: bytes) -> str | None:
  if len(head) >= 5 and head[:5] == b"%PDF-":
    return "application/pdf"
  if len(head) >= 8 and head[:8] == b"\x89PNG\r\n\x1a\n":
    return "image/png"
  if len(head) >= 3 and head[:3] == b"\xff\xd8\xff":
    return "image/jpeg"
  if len(head) >= 6 and head[:6] in (b"GIF87a", b"GIF89a"):
    return "image/gif"
  if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
    return "image/webp"
  if len(head) >= 2 and head[:2] == b"BM":
    return "image/bmp"
  if len(head) >= 12 and head[4:8] == b"ftyp":
    return "video/mp4"
  if len(head) >= 4 and head[:4] == b"\x1a\x45\xdf\xa3":
    return "video/webm"
  if len(head) >= 4 and head[:4] == b"OggS":
    return "audio/ogg"
  if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
    return "audio/wav"
  if len(head) >= 4 and head[:4] == b"fLaC":
    return "audio/flac"
  if len(head) >= 3 and head[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
    return "audio/mpeg"
  return None


def _infer_mime(path: Path, data: bytes) -> tuple[str, list[str]]:
  warnings: list[str] = []
  head = data[:512]
  guess, _ = mimetypes.guess_type(path.name, strict=False)
  sniff = _sniff_mime(head)

  if sniff and (not guess or guess == "application/octet-stream"):
    return sniff, warnings
  if guess and guess != "application/octet-stream":
    return guess, warnings
  if sniff:
    return sniff, warnings
  if guess:
    return guess, warnings
  warnings.append(
      f"could not infer MIME type for {path}; using application/octet-stream "
      "(Gemini may reject unsupported types)"
  )
  return "application/octet-stream", warnings


def _is_supported_mime(m: str) -> bool:
  mm = (m or "").strip().lower()
  if not mm:
    return False
  if mm.startswith(("image/", "audio/", "video/", "text/")):
    return True
  if mm == "application/pdf":
    return True
  # Most other application/* types are rejected by Gemini file parts.
  return False


def build_user_content(
  prompt: str,
  attachment_paths: Sequence[Path | str] | None,
  *,
  project_root: Path,
) -> tuple[types.Content, list[str]]:
  """
  Build ``Content`` with inline file parts first, then the text part.

  Returns ``(content, warnings)`` — warnings are non-fatal skips or hints (stderr them).
  """
  warnings: list[str] = []
  parts: list[types.Part] = []
  max_b = _max_attachment_bytes()
  if attachment_paths:
    for raw in list(attachment_paths)[:_MAX_ATTACHMENTS]:
      p = resolve_attachment_path(raw, project_root=project_root)
      if not p.is_file():
        warnings.append(f"attachment not found: {raw}")
        continue
      try:
        size = p.stat().st_size
      except OSError as e:
        warnings.append(f"attachment stat failed {p}: {e}")
        continue
      if size > max_b:
        warnings.append(
            f"attachment too large ({size} bytes, max {max_b}): {p} "
            "(set GEMCODE_MAX_ATTACHMENT_BYTES or use a smaller file)"
        )
        continue
      try:
        data = p.read_bytes()
      except OSError as e:
        warnings.append(f"attachment read failed {p}: {e}")
        continue
      mime, mw = _infer_mime(p, data)
      warnings.extend(mw)
      if not _is_supported_mime(mime) or mime == "application/octet-stream":
        warnings.append(
          f"unsupported attachment type for {p} (mime={mime}); "
          "skipping (export to PDF/image/text if needed)"
        )
        continue
      parts.append(types.Part(inline_data=types.Blob(data=data, mime_type=mime)))

  text = (prompt or "").strip() or (
      "(User attached file(s) only — describe or analyze them.)" if parts else ""
  )
  parts.append(types.Part(text=text))
  return types.Content(role="user", parts=parts), warnings
