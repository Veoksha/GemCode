"""
Ensure pytest imports this repo's `gemcode` from `./src`, not an older user/site install.
"""

from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent
_src = _root / "src"
if _src.is_dir():
  p = str(_src)
  if p not in sys.path:
    sys.path.insert(0, p)
