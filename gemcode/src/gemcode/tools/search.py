"""Grep content with regex — rg-backed when available, Python fallback."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from gemcode.config import GemCodeConfig


def _find_rg() -> str | None:
    """Locate ripgrep binary — checks PATH first, then common install locations."""
    found = shutil.which("rg")
    if found:
        return found
    for candidate in (
        "/usr/bin/rg",
        "/opt/homebrew/bin/rg",
        "/usr/local/bin/rg",
        "/home/linuxbrew/.linuxbrew/bin/rg",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def make_grep_tool(cfg: GemCodeConfig):
    root = cfg.project_root
    rg_bin = _find_rg()

    def grep_content(
        pattern: str,
        path_glob: str = "**/*",
        max_matches: int = 40,
        context_lines: int = 0,
        case_sensitive: bool = True,
    ) -> dict:
        """
        Search file contents with a regex pattern (backed by ripgrep when available).

        Use this instead of bash("grep -r pattern .") — it needs no permission
        and is instant. Binary files are skipped automatically.

        Parameters:
        - pattern:       Regex pattern (Python/ripgrep syntax). Use | for alternation.
        - path_glob:     File glob relative to project root (default: all files).
        - context_lines: Lines before+after each match (like grep -C). Use to see
                         surrounding code — e.g. context_lines=4 shows a function's body.
        - case_sensitive: False for case-insensitive search.
        - max_matches:   Cap on returned results (1–500, default 80).

        Examples:
          grep_content("def authenticate", "**/*.py", context_lines=4)
          grep_content("TODO|FIXME|HACK", "**/*.ts")
          grep_content("import React", "**/*.tsx", case_sensitive=False)
          grep_content("class.*Error", "**/*.py", context_lines=2)
          grep_content("useState", "src/**/*.tsx", context_lines=3)

        Issue multiple grep_content calls in the same turn when searching for
        different patterns — they run in parallel.
        """
        if max_matches < 1:
            max_matches = 1
        if max_matches > 500:
            max_matches = 500
        if context_lines < 0:
            context_lines = 0
        if context_lines > 20:
            context_lines = 20

        try:
            re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
        except re.error as e:
            return {"error": f"Invalid regex: {e}"}

        # Prefer ripgrep (faster, handles binary, respects .gitignore)
        if rg_bin:
            try:
                cmd = [
                    rg_bin,
                    "-n",
                    "--glob", path_glob,
                    "--glob", "!.git/*",
                ]
                if not case_sensitive:
                    cmd.append("-i")
                if context_lines > 0:
                    cmd.extend(["-C", str(context_lines)])
                cmd.extend([pattern, "."])
                proc = subprocess.run(
                    cmd,
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                lines = proc.stdout.splitlines()[:max_matches * (1 + 2 * context_lines + 2)]
                # Re-cap to max_matches counting only match lines (not context)
                return {
                    "pattern": pattern,
                    "matches": lines[:max_matches * max(1, 1 + 2 * context_lines)],
                    "backend": "rg",
                }
            except (subprocess.TimeoutExpired, OSError):
                pass

        # Python fallback
        flags = 0 if case_sensitive else re.IGNORECASE
        rx = re.compile(pattern, flags)
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
            file_lines = text.splitlines()
            for i, line in enumerate(file_lines):
                if rx.search(line):
                    try:
                        rel = fp.resolve().relative_to(root)
                    except ValueError:
                        rel = fp
                    if context_lines > 0:
                        # Add separator and context block
                        matches.append("--")
                        for ci in range(max(0, i - context_lines), i):
                            matches.append(f"{rel}:{ci + 1}-{file_lines[ci][:400]}")
                        matches.append(f"{rel}:{i + 1}:{line[:500]}")
                        for ci in range(i + 1, min(len(file_lines), i + 1 + context_lines)):
                            matches.append(f"{rel}:{ci + 1}-{file_lines[ci][:400]}")
                    else:
                        matches.append(f"{rel}:{i + 1}:{line[:500]}")

                    if len(matches) >= max_matches * max(1, 1 + 2 * context_lines):
                        return {
                            "pattern": pattern,
                            "matches": matches,
                            "truncated": True,
                            "backend": "python",
                        }
        return {"pattern": pattern, "matches": matches, "backend": "python"}

    return grep_content
