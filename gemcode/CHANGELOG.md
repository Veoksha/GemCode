## 0.4.12

- **Habits / fleet inbox**: `job.report` lines include **member** and **habit** metadata; **`GEMCODE_FLEET_REPORTS_AUTO_CONTINUE` defaults to on** so the manager can digest background output after assistant turns (set `=0` to opt out). Friendlier digest prompt; docs and `habits_add` help updated.

## 0.4.11

- **UX**: `GEMCODE_AFC_PROMPT` defaults to **off** — no blocking `afc>` stdin when MCP/OpenAPI toolsets are present (keeps all tools; use `GEMCODE_AFC_PROMPT=1` to restore the prompt). Docs and `/afc` help updated.

## 0.4.10

- **Docs**: Agent Mesh on a **background thread** (habits/triggers while TUI waits); **`org_delegate`** is mesh-only (no runtime IPC); **`agent_dm`** / **`agent_broadcast`** and fleet inbox formatting; clarified **`/agent assign|trigger`** IPC vs mesh fallback; **`agent_mesh.py`** module docstring aligned.

## 0.4.9

- **Agents**: Delegation no longer depends on Kaira; agent direct messaging and broadcast support.

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

