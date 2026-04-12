"""Multimodal user content (inline files: images, PDF, etc.)."""

from __future__ import annotations

from pathlib import Path

from gemcode.multimodal_input import (
  build_user_content,
  resolve_attachment_path,
  resolve_image_path,
)


def test_resolve_attachment_relative_to_project_root(tmp_path: Path) -> None:
  rel = tmp_path / "assets" / "a.png"
  rel.parent.mkdir(parents=True)
  rel.write_bytes(b"x")
  got = resolve_attachment_path("assets/a.png", project_root=tmp_path)
  assert got.resolve() == rel.resolve()
  got2 = resolve_image_path("assets/a.png", project_root=tmp_path)
  assert got2.resolve() == rel.resolve()


def test_build_user_content_png(tmp_path: Path) -> None:
  img = tmp_path / "x.png"
  img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
  content, warns = build_user_content("describe it", [img], project_root=tmp_path)
  assert not warns
  assert content.role == "user"
  assert len(content.parts) == 2
  assert content.parts[0].inline_data is not None
  assert content.parts[0].inline_data.mime_type == "image/png"
  assert content.parts[1].text == "describe it"


def test_build_user_content_pdf_by_extension(tmp_path: Path) -> None:
  pdf = tmp_path / "doc.pdf"
  pdf.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
  content, warns = build_user_content("summarize", [pdf], project_root=tmp_path)
  assert not warns
  assert content.parts[0].inline_data.mime_type == "application/pdf"


def test_build_user_content_sniffs_pdf_without_extension(tmp_path: Path) -> None:
  blob = tmp_path / "download"
  blob.write_bytes(b"%PDF-1.4\n")
  content, warns = build_user_content("go", [blob], project_root=tmp_path)
  assert not warns
  assert content.parts[0].inline_data.mime_type == "application/pdf"


def test_build_user_content_unknown_mime_warns(tmp_path: Path) -> None:
  f = tmp_path / "opaque.zzzq"
  f.write_bytes(b"\xde\xad\xbe\xef" * 20)
  content, warns = build_user_content("h", [f], project_root=tmp_path)
  assert warns and any("could not infer MIME" in w for w in warns)
  assert content.parts[0].inline_data.mime_type == "application/octet-stream"


def test_build_user_content_missing_file(tmp_path: Path) -> None:
  content, warns = build_user_content("hi", ["nope.png"], project_root=tmp_path)
  assert warns
  assert len(content.parts) == 1
  assert content.parts[0].text == "hi"
