"""Grep content with regex (MVP: Python scan, optional rg)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from gemcode.config import GemCodeConfig


def make_grep_tool(cfg: GemCodeConfig):
  root = cfg.project_root

  def grep_content(
    pattern: str,
    path_glob: str = "**/*",
    max_matches: int = 80,
  ) -> dict:
    """
    Search file contents with a regex pattern. Scans files under path_glob
    (glob relative to project root). Binary files skipped.
    """
    if max_matches < 1:
      max_matches = 1
    if max_matches > 500:
      max_matches = 500
    try:
      re.compile(pattern)
    except re.error as e:
      return {"error": f"Invalid regex: {e}"}

    # Prefer ripgrep if available (faster)
    rg = Path("/usr/bin/rg") if Path("/usr/bin/rg").is_file() else None
    if rg is None:
      rg = Path("/opt/homebrew/bin/rg") if Path("/opt/homebrew/bin/rg").is_file() else None
    if rg and rg.is_file():
      try:
        proc = subprocess.run(
          [
            str(rg),
            "-n",
            "--glob",
            path_glob,
            "--glob",
            "!.git/*",
            pattern,
            ".",
          ],
          cwd=root,
          capture_output=True,
          text=True,
          timeout=60,
          check=False,
        )
        lines = proc.stdout.splitlines()[:max_matches]
        return {"pattern": pattern, "matches": lines, "backend": "rg"}
      except (subprocess.TimeoutExpired, OSError):
        pass

    rx = re.compile(pattern)
    matches: list[str] = []
    for fp in root.glob(path_glob):
      if not fp.is_file():
        continue
      if fp.stat().st_size > 2_000_000:
        continue
      try:
        text = fp.read_text(encoding="utf-8", errors="ignore")
      except OSError:
        continue
      for i, line in enumerate(text.splitlines(), 1):
        if rx.search(line):
          rel = fp.resolve().relative_to(root)
          matches.append(f"{rel}:{i}:{line[:500]}")
          if len(matches) >= max_matches:
            return {
              "pattern": pattern,
              "matches": matches,
              "truncated": True,
              "backend": "python",
            }
    return {"pattern": pattern, "matches": matches, "backend": "python"}

  return grep_content
