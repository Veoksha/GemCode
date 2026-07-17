#!/usr/bin/env sh
set -eu

ROOT="${GEMCODE_HOSTED_TENANT_ROOT:-/mnt/workspace}"
export GEMCODE_HOSTED_TENANT_ROOT="$ROOT"
export GEMCODE_WEB_PROJECT_ROOT="$ROOT"
export GEMCODE_HOME="${GEMCODE_HOME:-$ROOT/.gemcode}"
export GEMCODE_SUPER_MODE="${GEMCODE_SUPER_MODE:-1}"

mkdir -p "$GEMCODE_HOME"

# Auto-trust tenant workspace (hosted pods are isolated sandboxes — no interactive prompt).
python - <<'PY' || true
import os
from pathlib import Path

root = Path(os.environ["GEMCODE_HOSTED_TENANT_ROOT"]).resolve()
os.environ.setdefault("GEMCODE_HOME", str(root / ".gemcode"))

try:
    from gemcode.trust import ensure_hosted_workspace_trust
    ensure_hosted_workspace_trust(root)
except Exception:
    import json
    p = Path(os.environ["GEMCODE_HOME"]) / "trust.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"trusted_roots": [str(root)]}, indent=2) + "\n",
        encoding="utf-8",
    )
print(f"[entrypoint] trusted workspace {root}")
PY

exec gemcode serve -C "$ROOT" --host "${GEMCODE_WEB_API_HOST:-0.0.0.0}" --port "${GEMCODE_WEB_API_PORT:-3001}"
