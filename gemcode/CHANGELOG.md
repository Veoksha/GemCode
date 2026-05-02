## 0.4.2

- **Repo hygiene**: `run-gemcode-tui.sh` is no longer tracked; add your own local copy or use `pip install -e gemcode` / `PYTHONPATH=gemcode/src python -m gemcode.cli`.

## 0.4.1

- **PyPI**: New version so uploads succeed — PyPI never allows replacing `gemcode-0.4.0-*.whl` / the sdist once published ([file name reuse](https://pypi.org/help/#file-name-reuse)).
- **Orchestration / IPC**: Fleet manager socket resolution via `fleet_manager_ipc_path()` — prefers `.gemcode/manager_ipc.txt` (written when `gemcode runtime` starts at fleet root), then `<fleet_root>/.gemcode/ipc.sock`, then `GEMCODE_KAIRA_SOCKET` only as fallback. Runtime bind uses `--socket` / default layout and **does not** bind from `GEMCODE_KAIRA_SOCKET`.
- **UX**: Clearer `/runtime`, `/agent start`, and trigger/assign error text; TUI auto-connect uses the same fleet manager path logic.

## 0.3.121

- Fleet report inbox (`.gemcode/fleet_reports.jsonl`): drain into manager turns, optional auto-continue (`GEMCODE_FLEET_REPORTS_*`), writers from org/subtask/runtime paths.
- `/agents` as an alias for `/agent`; `/agent trigger` documented alongside assign/send.
- Docs: orchestration, configuration, operations, CLI/REPL, fleet verification; Research.md and README map updates.
- Tests: `gemcode/tests/test_fleet_reports.py` for inbox drain and `resolve_fleet_root`.

## 0.2.0

- Add GemCode terminal TUI (scrollback-style UI in `tui/scrollback.py`; no separate fullscreen mode in-tree).
- Add folder trust prompt and persisted trust store.
- Improve in-run permission confirmations and tool rendering.

