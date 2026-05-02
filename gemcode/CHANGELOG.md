## 0.3.121

- Fleet report inbox (`.gemcode/fleet_reports.jsonl`): drain into manager turns, optional auto-continue (`GEMCODE_FLEET_REPORTS_*`), writers from org/subtask/runtime paths.
- `/agents` as an alias for `/agent`; `/agent trigger` documented alongside assign/send.
- Docs: orchestration, configuration, operations, CLI/REPL, fleet verification; Research.md and README map updates.
- Tests: `gemcode/tests/test_fleet_reports.py` for inbox drain and `resolve_fleet_root`.

## 0.2.0

- Add GemCode terminal TUI (scrollback-style UI in `tui/scrollback.py`; no separate fullscreen mode in-tree).
- Add folder trust prompt and persisted trust store.
- Improve in-run permission confirmations and tool rendering.

