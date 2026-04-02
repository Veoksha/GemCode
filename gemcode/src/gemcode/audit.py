"""Structured audit log for tool invocations."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def append_audit(project_root: Path, record: dict[str, Any]) -> None:
  log_dir = project_root / ".gemcode"
  log_dir.mkdir(parents=True, exist_ok=True)
  path = log_dir / "audit.log"
  line = json.dumps({"ts": time.time(), **record}, ensure_ascii=False)
  path.open("a", encoding="utf-8").write(line + "\n")
