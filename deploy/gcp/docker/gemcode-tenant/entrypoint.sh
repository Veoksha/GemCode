#!/usr/bin/env sh
set -eu

ROOT="${GEMCODE_HOSTED_TENANT_ROOT:-/mnt/workspace}"
mkdir -p "$ROOT/.gemcode"

export GEMCODE_HOSTED_TENANT_ROOT="$ROOT"
export GEMCODE_WEB_PROJECT_ROOT="$ROOT"

# Trust workspace on first boot (hosted pods are pre-provisioned sandboxes).
if [ ! -f "$ROOT/.gemcode/trust.json" ]; then
  python - <<'PY' || true
import json, os
from pathlib import Path
root = Path(os.environ["GEMCODE_HOSTED_TENANT_ROOT"])
p = root / ".gemcode" / "trust.json"
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps({"trusted": True, "root": str(root.resolve())}), encoding="utf-8")
print(f"[entrypoint] trusted workspace {root}")
PY
fi

exec gemcode serve -C "$ROOT" --host "${GEMCODE_WEB_API_HOST:-0.0.0.0}" --port "${GEMCODE_WEB_API_PORT:-3001}"
