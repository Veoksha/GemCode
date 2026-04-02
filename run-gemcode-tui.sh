#!/usr/bin/env bash
set -euo pipefail

# GemCode TUI bootstrap runner.
#
# This script exists because macOS Terminal.app can fail with:
# - Python getpath "failed to make path absolute"
# - PermissionError: Operation not permitted (Desktop folder privacy)
#
# It forces a real absolute cwd, clears problematic env vars, and uses an
# explicit PYTHONPATH so `python -m gemcode.cli` works without editable installs.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

cd "$ROOT_DIR"

REALPWD="$(pwd -P)"

# If Terminal.app lacks access to Desktop/Documents, Python can throw
# PermissionError when importing from this folder. Detect early.
if ! /bin/ls -la "$REALPWD" >/dev/null 2>&1; then
  echo ""
  echo "[gemcode] ERROR: Your terminal does not have permission to access:"
  echo "  $REALPWD"
  echo ""
  echo "Fix:"
  echo "  System Settings → Privacy & Security → Files and Folders"
  echo "  Enable 'Terminal' for Desktop Folder (or grant Full Disk Access)."
  echo ""
  echo "Workaround:"
  echo "  Move this repo to ~/Projects (outside Desktop) and retry."
  echo ""
  exit 2
fi

PY="${GEMCODE_PYTHON:-/usr/local/bin/python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  # If user passed a non-path in GEMCODE_PYTHON, fall back to python3 in PATH.
  PY="${GEMCODE_PYTHON:-python3}"
fi

exec env \
  -u PWD \
  -u __PYVENV_LAUNCHER__ \
  -u PYTHONHOME \
  -u PYTHONPATH \
  PWD="$REALPWD" \
  GEMCODE_TUI=1 \
  PYTHONPATH="$REALPWD/gemcode/src" \
  "$PY" -m gemcode.cli "$@"

