from __future__ import annotations

import json
import os
from pathlib import Path


def _trust_file_path() -> Path:
  base = Path(os.environ.get("GEMCODE_HOME") or (Path.home() / ".gemcode"))
  return base / "trust.json"


def trust_json_path() -> Path:
  """Path to the trust database (respects GEMCODE_HOME)."""
  return _trust_file_path()


def _hosted_tenant_root() -> Path | None:
  """Locked workspace root when ``GEMCODE_HOSTED_TENANT_ROOT`` is set."""
  raw = os.environ.get("GEMCODE_HOSTED_TENANT_ROOT", "").strip()
  if not raw:
    return None
  return Path(raw).expanduser().resolve()


def _is_within_root(root: Path, candidate: Path) -> bool:
  try:
    candidate.resolve().relative_to(root.resolve())
    return True
  except ValueError:
    return candidate.resolve() == root.resolve()


def is_hosted_mode() -> bool:
  return _hosted_tenant_root() is not None


def load_trusted_roots() -> set[str]:
  """
  Returns a set of resolved absolute paths (as strings) that are trusted.
  """
  p = _trust_file_path()
  try:
    data = json.loads(p.read_text("utf-8"))
  except FileNotFoundError:
    return set()
  except Exception:
    return set()
  roots = data.get("trusted_roots") if isinstance(data, dict) else None
  if not isinstance(roots, list):
    return set()
  out: set[str] = set()
  for r in roots:
    if isinstance(r, str) and r:
      out.add(str(Path(r).resolve()))
  return out


def save_trusted_roots(roots: set[str]) -> None:
  p = _trust_file_path()
  p.parent.mkdir(parents=True, exist_ok=True)
  payload = {"trusted_roots": sorted(set(str(Path(r).resolve()) for r in roots))}
  p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def is_trusted_root(root: Path) -> bool:
  r = root.resolve()
  locked = _hosted_tenant_root()
  if locked is not None and _is_within_root(locked, r):
    return True
  return str(r) in load_trusted_roots()


def ensure_hosted_workspace_trust(root: Path | None = None) -> bool:
  """
  In hosted multi-tenant mode, trust the tenant workspace automatically.

  Interactive folder-trust prompts are not used on shared infrastructure — the
  platform already isolates each user to their PVC. Persists trust for tooling
  that reads ``trust.json`` directly.
  """
  locked = _hosted_tenant_root()
  if locked is None:
    return False
  target = (root or locked).resolve()
  if not _is_within_root(locked, target):
    target = locked
  trust_root(locked, trusted=True)
  if target != locked:
    trust_root(target, trusted=True)
  return True


def trust_root(root: Path, *, trusted: bool) -> None:
  roots = load_trusted_roots()
  r = str(root.resolve())
  if trusted:
    roots.add(r)
  else:
    roots.discard(r)
  save_trusted_roots(roots)

