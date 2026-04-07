"""
Jupyter notebook tools — read and edit .ipynb files.

Analogous to Reference UI NotebookEdit (reference terminal UI handles read via the main
Read tool; GemCode provides dedicated notebook_read + notebook_edit tools).

Jupyter notebooks are JSON files with a cells array. Each cell has:
  - cell_type: "code" | "markdown" | "raw"
  - id: unique string (nbformat 4.5+)
  - source: list of strings (lines)
  - outputs: list of output objects (code cells only)
  - execution_count: int or null (code cells only)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig
from gemcode.paths import PathEscapeError, resolve_under_root


def _load_notebook(path: Path) -> tuple[dict, str | None]:
    """Load and parse a notebook. Returns (nb_dict, error_or_None)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        return {}, f"Cannot read file: {e}"
    try:
        nb = json.loads(raw)
    except json.JSONDecodeError as e:
        return {}, f"Invalid JSON: {e}"
    if not isinstance(nb, dict) or "cells" not in nb:
        return {}, "File does not look like a Jupyter notebook (missing 'cells' key)"
    return nb, None


def _cell_source(cell: dict) -> str:
    src = cell.get("source", "")
    if isinstance(src, list):
        return "".join(src)
    return str(src)


def _format_output(out: dict) -> str:
    """Format a single cell output for display."""
    out_type = out.get("output_type", "")
    if out_type in ("stream",):
        text = out.get("text", "")
        if isinstance(text, list):
            text = "".join(text)
        return f"[{out.get('name', 'stdout')}] {text[:500]}"
    if out_type in ("execute_result", "display_data"):
        data = out.get("data", {})
        if "text/plain" in data:
            txt = data["text/plain"]
            if isinstance(txt, list):
                txt = "".join(txt)
            return txt[:500]
        return f"[{out_type}]"
    if out_type == "error":
        return f"[ERROR] {out.get('ename')}: {out.get('evalue')}"
    return f"[{out_type}]"


def make_notebook_tools(cfg: GemCodeConfig):
    root = cfg.project_root

    def notebook_read(path: str, include_outputs: bool = True) -> dict[str, Any]:
        """
        Read a Jupyter notebook (.ipynb) and return its cells in a readable format.

        Returns each cell with: index, id, type (code/markdown/raw), source text,
        and optionally the cell outputs (stdout, results, errors).

        Use this instead of read_file for .ipynb files — read_file returns raw JSON
        which is hard to parse. notebook_read gives you clean, structured output.

        Args:
            path: Path to the .ipynb file (relative to project root).
            include_outputs: If True (default), include cell execution outputs.
        """
        if not path or not path.strip():
            return {"error": "path must not be empty"}
        try:
            nb_path = resolve_under_root(root, path.strip())
        except PathEscapeError as e:
            return {"error": str(e)}
        if not nb_path.exists():
            return {"error": f"File not found: {path}"}
        if nb_path.suffix.lower() != ".ipynb":
            return {"error": "File must be a .ipynb notebook"}

        nb, err = _load_notebook(nb_path)
        if err:
            return {"error": err}

        cells = nb.get("cells", [])
        formatted: list[dict[str, Any]] = []
        for idx, cell in enumerate(cells):
            cell_id = cell.get("id", f"cell_{idx}")
            cell_type = cell.get("cell_type", "code")
            source = _cell_source(cell)
            entry: dict[str, Any] = {
                "index": idx,
                "id": cell_id,
                "type": cell_type,
                "source": source,
            }
            if include_outputs and cell_type == "code":
                outputs = cell.get("outputs", [])
                if outputs:
                    entry["outputs"] = [_format_output(o) for o in outputs[:10]]
                    entry["execution_count"] = cell.get("execution_count")
            formatted.append(entry)

        nbformat = nb.get("nbformat", "?")
        kernel = nb.get("metadata", {}).get("kernelspec", {}).get("display_name", "unknown")
        return {
            "path": path,
            "nbformat": nbformat,
            "kernel": kernel,
            "cell_count": len(cells),
            "cells": formatted,
        }

    def notebook_edit(
        path: str,
        cell_index: int,
        new_source: str,
        cell_type: str = "",
        edit_mode: str = "replace",
    ) -> dict[str, Any]:
        """
        Edit a cell in a Jupyter notebook (.ipynb).

        Supports three edit modes (analogous to Reference UI NotebookEdit):
        - "replace" (default): Replace the source of the cell at cell_index.
        - "insert": Insert a new cell at cell_index (shifts existing cells down).
        - "delete": Delete the cell at cell_index (new_source is ignored).

        Args:
            path: Path to the .ipynb file (relative to project root).
            cell_index: 0-based index of the cell to edit/insert/delete.
            new_source: New source code or markdown text for the cell.
            cell_type: Cell type — "code", "markdown", or "raw". Only used
                       for "insert" mode (ignored for replace/delete).
            edit_mode: "replace", "insert", or "delete".

        Returns a summary of the change (new cell count, affected cell id).
        """
        if not path or not path.strip():
            return {"error": "path must not be empty"}
        valid_modes = ("replace", "insert", "delete")
        if edit_mode not in valid_modes:
            return {"error": f"edit_mode must be one of: {', '.join(valid_modes)}"}
        if cell_type and cell_type not in ("code", "markdown", "raw"):
            return {"error": "cell_type must be 'code', 'markdown', or 'raw'"}

        try:
            nb_path = resolve_under_root(root, path.strip())
        except PathEscapeError as e:
            return {"error": str(e)}
        if not nb_path.exists():
            return {"error": f"File not found: {path}"}
        if nb_path.suffix.lower() != ".ipynb":
            return {"error": "File must be a .ipynb notebook"}

        nb, err = _load_notebook(nb_path)
        if err:
            return {"error": err}

        cells = nb.get("cells", [])
        nbformat_minor = nb.get("nbformat_minor", 4)

        if edit_mode == "replace":
            if cell_index < 0 or cell_index >= len(cells):
                return {
                    "error": f"cell_index {cell_index} out of range (notebook has {len(cells)} cells)"
                }
            old_source = _cell_source(cells[cell_index])
            cells[cell_index]["source"] = new_source
            # Clear outputs after editing a code cell (stale results)
            if cells[cell_index].get("cell_type") == "code":
                cells[cell_index]["outputs"] = []
                cells[cell_index]["execution_count"] = None
            affected_id = cells[cell_index].get("id", f"cell_{cell_index}")
            result_msg = f"Replaced source of cell {cell_index} ({affected_id})"

        elif edit_mode == "insert":
            if cell_index < 0 or cell_index > len(cells):
                return {
                    "error": f"cell_index {cell_index} out of range for insert (0 to {len(cells)})"
                }
            ct = cell_type or "code"
            import uuid as _uuid
            new_id = str(_uuid.uuid4())[:8]
            new_cell: dict[str, Any] = {
                "cell_type": ct,
                "id": new_id,
                "source": new_source,
                "metadata": {},
            }
            if ct == "code":
                new_cell["outputs"] = []
                new_cell["execution_count"] = None
            cells.insert(cell_index, new_cell)
            affected_id = new_id
            result_msg = f"Inserted new {ct} cell at index {cell_index} (id={new_id})"

        elif edit_mode == "delete":
            if cell_index < 0 or cell_index >= len(cells):
                return {
                    "error": f"cell_index {cell_index} out of range (notebook has {len(cells)} cells)"
                }
            affected_id = cells[cell_index].get("id", f"cell_{cell_index}")
            del cells[cell_index]
            result_msg = f"Deleted cell {cell_index} ({affected_id})"
        else:
            return {"error": f"Unknown edit_mode: {edit_mode}"}

        nb["cells"] = cells

        # Write back
        try:
            nb_path.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:
            return {"error": f"Failed to write notebook: {e}"}

        return {
            "ok": True,
            "path": path,
            "edit_mode": edit_mode,
            "cell_index": cell_index,
            "cell_id": affected_id,
            "cell_count": len(nb["cells"]),
            "message": result_msg,
        }

    return notebook_read, notebook_edit
