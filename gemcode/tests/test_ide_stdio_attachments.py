"""IDE stdio inline (base64) attachments → temp files for multimodal."""

from __future__ import annotations

import base64

from gemcode.ide_stdio import prepare_inline_attachment_paths


def test_prepare_inline_pdf_roundtrip(monkeypatch) -> None:
  monkeypatch.delenv("GEMCODE_MAX_ATTACHMENT_BYTES", raising=False)
  raw = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
  b64 = base64.b64encode(raw).decode("ascii")
  paths, errs = prepare_inline_attachment_paths(
      [
          {
              "type": "inline",
              "filename": "x.pdf",
              "mimeType": "application/pdf",
              "data": b64,
          }
      ],
      max_bytes=1024 * 1024,
  )
  assert not errs
  assert len(paths) == 1
  try:
    assert paths[0].read_bytes() == raw
  finally:
    paths[0].unlink(missing_ok=True)


def test_prepare_inline_rejects_oversized(monkeypatch) -> None:
  monkeypatch.delenv("GEMCODE_MAX_ATTACHMENT_BYTES", raising=False)
  raw = b"x" * 100
  b64 = base64.b64encode(raw).decode("ascii")
  paths, errs = prepare_inline_attachment_paths(
      [{"type": "blob", "data": b64}],
      max_bytes=20,
  )
  assert errs
  assert not paths
