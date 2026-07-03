"""Workspace file browser API for web UIs (local and hosted tenants)."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import subprocess
from pathlib import Path
from typing import Any

from gemcode.web.project_root import HostedTenantPathError, resolve_web_project_root

ALWAYS_HIDDEN = {
  ".git",
  "node_modules",
  ".next",
  ".turbo",
  ".cache",
  "__pycache__",
  ".DS_Store",
  "Thumbs.db",
}

MAX_READ_BYTES = 10 * 1024 * 1024
MAX_WRITE_BYTES = 5 * 1024 * 1024
MAX_TREE_DEPTH = 8


def _resolve_root(raw_path: str | None) -> Path:
  return resolve_web_project_root(raw_path)


def _resolve_under_root(root: Path, relative: str) -> Path:
  rel = (relative or "").strip().lstrip("/")
  candidate = (root / rel).resolve()
  try:
    candidate.relative_to(root.resolve())
  except ValueError as exc:
    raise HostedTenantPathError(f"path is outside workspace: {candidate}") from exc
  return candidate


def _git_status_map(root: Path) -> dict[str, str]:
  try:
    proc = subprocess.run(
      ["git", "status", "--porcelain", "-u"],
      cwd=str(root),
      capture_output=True,
      text=True,
      timeout=5,
      check=False,
    )
  except (OSError, subprocess.TimeoutExpired):
    return {}
  out: dict[str, str] = {}
  for line in (proc.stdout or "").splitlines():
    if not line:
      continue
    xy = line[:2].strip()
    file_path = line[3:].strip().strip('"')
    if not xy or not file_path:
      continue
    status = xy[0] if xy[0] not in (" ", "?") else xy[1] if xy[1] != " " else "?"
    out[file_path] = "?" if status == "?" else status
  return out


def _ignore_patterns(root: Path) -> list[str]:
  p = root / ".gitignore"
  if not p.is_file():
    return []
  try:
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
  except OSError:
    return []
  return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def _matches_ignore(name: str, patterns: list[str]) -> bool:
  for pattern in patterns:
    p = pattern[1:] if pattern.startswith("/") else pattern
    if p == name:
      return True
    if p.endswith("/") and p[:-1] == name:
      return True
    if "*" in p:
      import re

      rx = "^" + re.escape(p).replace(r"\*", "[^/]*") + "$"
      if re.match(rx, name):
        return True
  return False


def _build_tree(
  dir_path: Path,
  root: Path,
  *,
  git_map: dict[str, str],
  ignore_patterns: list[str],
  show_ignored: bool,
  depth: int = 0,
) -> list[dict[str, Any]]:
  if depth > MAX_TREE_DEPTH:
    return []
  try:
    entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
  except OSError:
    return []
  nodes: list[dict[str, Any]] = []
  for entry in entries:
    name = entry.name
    if name in ALWAYS_HIDDEN:
      continue
    if not show_ignored and _matches_ignore(name, ignore_patterns):
      continue
    if not show_ignored and name.startswith(".") and name != ".env.example":
      continue
    rel = entry.relative_to(root).as_posix()
    git_status = git_map.get(rel)
    if entry.is_dir():
      children = _build_tree(
        entry,
        root,
        git_map=git_map,
        ignore_patterns=ignore_patterns,
        show_ignored=show_ignored,
        depth=depth + 1,
      )
      nodes.append(
        {
          "name": name,
          "path": rel,
          "type": "directory",
          "children": children,
          "gitStatus": git_status,
        }
      )
    elif entry.is_file():
      nodes.append(
        {
          "name": name,
          "path": rel,
          "type": "file",
          "gitStatus": git_status,
        }
      )
  return nodes


def handle_files_tree_get(
  raw_path: str | None,
  *,
  cwd: str = "",
  show_ignored: bool = False,
) -> tuple[int, dict[str, Any]]:
  try:
    root = _resolve_root(raw_path)
  except HostedTenantPathError as exc:
    return 403, {"error": str(exc)}
  if not root.is_dir():
    return 400, {"error": "workspace is not a directory"}
  try:
    resolved_cwd = _resolve_under_root(root, cwd)
  except HostedTenantPathError as exc:
    return 403, {"error": str(exc)}
  if not resolved_cwd.is_dir():
    return 400, {"error": "Invalid cwd"}

  git_map = _git_status_map(root)
  ignore_patterns = _ignore_patterns(root)
  tree = _build_tree(
    resolved_cwd,
    root,
    git_map=git_map,
    ignore_patterns=ignore_patterns,
    show_ignored=show_ignored,
  )
  rel = resolved_cwd.relative_to(root)
  segments = [] if str(rel) == "." else str(rel).split("/")
  breadcrumbs = [root.name or str(root), *segments]
  breadcrumb_rels = [""] + [
    "/".join(segments[: i + 1]) for i in range(len(segments))
  ]
  return 200, {
    "tree": tree,
    "root": str(root),
    "cwd": cwd,
    "breadcrumbs": breadcrumbs,
    "breadcrumbRels": breadcrumb_rels,
  }


def handle_files_read_get(
  raw_path: str | None,
  file_path: str,
) -> tuple[int, dict[str, Any]]:
  if not file_path.strip():
    return 400, {"error": "path parameter required"}
  try:
    root = _resolve_root(raw_path)
    resolved = _resolve_under_root(root, file_path)
  except HostedTenantPathError as exc:
    return 403, {"error": str(exc)}
  if not resolved.is_file():
    return 404 if not resolved.exists() else 400, {"error": "path is not a file"}
  try:
    size = resolved.stat().st_size
  except OSError as exc:
    return 404, {"error": str(exc)}
  if size > MAX_READ_BYTES:
    return 413, {"error": f"File exceeds maximum readable size ({MAX_READ_BYTES // 1024 // 1024} MB)"}

  ext = resolved.suffix.lower().lstrip(".")
  mime, _ = mimetypes.guess_type(str(resolved))

  if ext in ("docx", "doc", "pdf"):
    data = base64.b64encode(resolved.read_bytes()).decode("ascii")
    kind = "pdf" if ext == "pdf" else "docx"
    return 200, {
      "content": data,
      "isImage": False,
      "kind": kind,
      "mimeType": mime or "application/octet-stream",
      "size": size,
      "modified": resolved.stat().st_mtime,
    }

  if ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "ico"):
    mime = mime or f"image/{ext}"
    data = base64.b64encode(resolved.read_bytes()).decode("ascii")
    return 200, {
      "content": f"data:{mime};base64,{data}",
      "isImage": True,
      "size": size,
      "modified": resolved.stat().st_mtime,
    }

  try:
    probe = resolved.read_bytes()[:512]
    if b"\x00" in probe:
      return 415, {"error": "Binary file cannot be read as text"}
    content = resolved.read_text(encoding="utf-8")
  except OSError as exc:
    return 404, {"error": str(exc)}
  except UnicodeDecodeError:
    return 415, {"error": "Binary file cannot be read as text"}

  return 200, {
    "content": content,
    "isImage": ext == "svg",
    "size": size,
    "modified": resolved.stat().st_mtime,
  }


def handle_files_write_post(data: dict[str, Any], raw_path: str | None) -> tuple[int, dict[str, Any]]:
  file_path = str(data.get("path") or "").strip()
  if not file_path:
    return 400, {"ok": False, "error": "path is required"}
  content = data.get("content")
  if content is None:
    content = ""
  if not isinstance(content, str):
    return 400, {"ok": False, "error": "content must be a string"}
  if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
    return 413, {"ok": False, "error": "content too large"}
  try:
    root = _resolve_root(raw_path)
    resolved = _resolve_under_root(root, file_path)
  except HostedTenantPathError as exc:
    return 403, {"ok": False, "error": str(exc)}
  try:
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
  except OSError as exc:
    return 500, {"ok": False, "error": str(exc)}
  return 200, {"ok": True, "path": file_path}
