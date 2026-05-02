"""
Checkpoints for GemCode.

Goal: make file mutations reversible with an explicit, local checkpoint log.

Storage:
  <project>/.gemcode/checkpoints/<checkpoint_id>/manifest.json
  <project>/.gemcode/checkpoints/<checkpoint_id>/files/<path>
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _now_ms() -> int:
  return int(time.time() * 1000)


def _checkpoints_dir(project_root: Path) -> Path:
  return project_root / ".gemcode" / "checkpoints"


def _safe_rel(project_root: Path, p: Path) -> str:
  return str(p.resolve().relative_to(project_root.resolve()))


@dataclass
class CheckpointFile:
  path: str
  existed: bool


@dataclass
class Checkpoint:
  id: str
  ts_ms: int
  op: str
  files: list[CheckpointFile]


def create_checkpoint(
  *,
  project_root: Path,
  op: str,
  file_snapshots: list[tuple[Path, bool]],
) -> Checkpoint:
  """
  Create a checkpoint capturing the *previous* contents of the provided files.

  file_snapshots entries are (absolute_path, existed_bool).
  """
  ts = _now_ms()
  cid = f"cp_{ts}"
  base = _checkpoints_dir(project_root) / cid
  files_dir = base / "files"
  files_dir.mkdir(parents=True, exist_ok=True)
  out_files: list[CheckpointFile] = []

  for abs_path, existed in file_snapshots:
    try:
      rel = _safe_rel(project_root, abs_path)
    except Exception:
      continue
    out_files.append(CheckpointFile(path=rel, existed=bool(existed)))
    if existed and abs_path.is_file():
      target = files_dir / rel
      target.parent.mkdir(parents=True, exist_ok=True)
      target.write_bytes(abs_path.read_bytes())

  manifest = {
    "id": cid,
    "ts_ms": ts,
    "op": op,
    "files": [{"path": f.path, "existed": f.existed} for f in out_files],
  }
  (base / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

  # Publish checkpoint event to the bus (enables trigger-based verification)
  try:
    from gemcode.event_bus import BusMessage, get_bus
    bus = get_bus()
    bus.publish_sync(BusMessage(
      topic="checkpoint.created",
      from_addr="checkpoints",
      payload={
        "checkpoint_id": cid,
        "op": op,
        "files": [f.path for f in out_files],
        "file_count": len(out_files),
      },
    ))
  except Exception:
    pass

  return Checkpoint(id=cid, ts_ms=ts, op=op, files=out_files)


def list_checkpoints(project_root: Path, limit: int = 20) -> list[dict[str, Any]]:
  d = _checkpoints_dir(project_root)
  if not d.is_dir():
    return []
  cps = []
  for p in sorted(d.iterdir(), key=lambda x: x.name, reverse=True):
    m = p / "manifest.json"
    if not m.is_file():
      continue
    try:
      obj = json.loads(m.read_text(encoding="utf-8"))
      cps.append(obj)
    except Exception:
      continue
    if len(cps) >= max(1, int(limit)):
      break
  return cps


def undo_checkpoint(project_root: Path, checkpoint_id: str = "") -> dict[str, Any]:
  d = _checkpoints_dir(project_root)
  if not d.is_dir():
    return {"error": "no_checkpoints"}
  if checkpoint_id:
    base = d / checkpoint_id
  else:
    # newest
    items = [p for p in d.iterdir() if p.is_dir()]
    if not items:
      return {"error": "no_checkpoints"}
    base = sorted(items, key=lambda x: x.name, reverse=True)[0]
  manifest_path = base / "manifest.json"
  if not manifest_path.is_file():
    return {"error": "checkpoint_missing_manifest"}
  try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  except Exception as e:
    return {"error": f"checkpoint_manifest_invalid:{e}"}
  files_dir = base / "files"
  restored = []
  for f in manifest.get("files") or []:
    try:
      rel = str(f.get("path") or "")
      existed = bool(f.get("existed"))
      abs_path = (project_root / rel).resolve()
      if existed:
        src = files_dir / rel
        if src.is_file():
          abs_path.parent.mkdir(parents=True, exist_ok=True)
          abs_path.write_bytes(src.read_bytes())
          restored.append(rel)
      else:
        # File did not exist previously; remove it if it exists now.
        if abs_path.is_file():
          abs_path.unlink()
          restored.append(rel)
    except Exception:
      continue
  return {"checkpoint_id": manifest.get("id") or base.name, "restored": restored}

