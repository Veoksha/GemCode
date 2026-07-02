## 0.4.17

- **Docs:** Multi-agent model—fleet **`habits.json`** vs per-member skills/workspaces/runtime ([`orchestration.md`](../docs/orchestration.md)); stopping background work (**`/mesh halt`**, **`mesh_halt`**, **`habits_clear_all`**); **`/fleet`** and **`/mesh`** in [`cli-and-repl.md`](../docs/cli-and-repl.md); mesh singleton **`project_root`** in [`configuration.md`](../docs/configuration.md); user manual section in **`README.md`**.
- **Mesh control:** **`halt_jobs`**, **`mesh_halt`** tool, **`/mesh`** slash, **`habits_clear_all`** — cancel running mesh tasks and drain the queue (habit removal alone does not stop in-flight work). Cancelled jobs do not emit **`job.report`** (fewer trigger follow-ups).

## 0.4.19

- **Web API:** Built-in HTTP server — **`gemcode serve`** and REPL **`/serve`** (`start` · `status` · `stop` · `url`). Default `http://127.0.0.1:3001`. Any frontend (official or custom) connects via `/api/*` — chat SSE, sessions, panel, preview, HITL, org/mesh, runtime, terminal. Implementation: `gemcode/src/gemcode/web/server.py` and `*_api.py` modules.
- **Docs:** [`web-ui-contract.md`](../docs/web-ui-contract.md), [`install.md`](../docs/install.md), [`cli-and-repl.md`](../docs/cli-and-repl.md), [`integrations.md`](../docs/integrations.md), [`configuration.md`](../docs/configuration.md) updated for serve; web UI repos gitignored (separate from PyPI package).
- **Fix:** `list_sessions` web API path (session DB resolution).
- **Doctor:** `/doctor` reports whether the web API is running.

## 0.4.20

- **Web API: reliability:** `gemcode serve` no longer kills long-running chat turns by default (no server-side turn timeout), emits periodic SSE keepalives to prevent proxy/browser idle disconnects, and increases HITL approval wait time (configurable).
- **Web API: port fallback:** If `3001` is busy, `gemcode serve` (and `/serve`) automatically binds the next available port (skips `3002` reserved for the web UI) and reports the URL to connect to. `/api/health` now includes `port` and `url`.
- **Web UI parity via serve:** Web requests can enable embeddings, maps grounding, and code executor; sessions API supports `new` and `resume`; workspace panel exposes a `tools` kind; web terminal accepts per-request permissions.

## 0.4.18

- **TUI timers:** Fix elapsed-time spinner freezing during long tool runs by making `bash` and `run_command` tools async/threaded (no longer block the TUI event loop). Added note in [`cli-and-repl.md`](../docs/cli-and-repl.md).

## 0.4.16

- **Mesh:** Nested **`delegate_to_member(wait=True)`** only on the mesh thread (manager loop no longer mis-runs worker lifecycle when `_mesh_job_depth > 0`). **Singleton** **`project_root`** sync on **`ensure_mesh` / `get_mesh`** + trigger **`reload`**.

## 0.4.15

- **Mesh:** Thread-safe **`enqueue`**; **`Future`-based** wait for **`org_delegate`**; nested inline delegation from mesh workers without scheduler deadlock.

## 0.4.14

- **TUI / fleet inbox**: **`/fleet`**, **`/fleet show`**, **`/fleet digest`** — drain or peek `.gemcode/fleet_reports.jsonl` without guessing (habits and mesh complete while idle at ❯; auto-continue only chains after assistant turns). Throttled **`job.report`** hint in the TUI (`GEMCODE_FLEET_TUI_NOTIFY`, `GEMCODE_FLEET_TUI_NOTIFY_MIN_S`). Bus **`job.report`** payloads include **`habit`** metadata when present.

## 0.4.13

- **Agent mesh**: **`GEMCODE_MESH_WORKER_UNATTENDED`** defaults to **on** (`1`): each mesh job uses non-blocking tool policy (`yes_to_all`, no interactive HITL) so background workers do not steal the main TUI for bash / `org_delegate` / writes. Set **`=0`** so mesh workers inherit the manager’s `--yes` / HITL settings.
- **Agent mesh / habits**: Serialize **`run_turn`** per ADK SQLite session (same `sessions.sqlite` + `user_id` + `session_id`). Concurrent habits or delegations for one agent no longer trip ADK **`SqliteSessionService.append_event`** optimistic-lock failures (“stale session” / `last_update_time`). Documented in **`configuration.md`**, **`orchestration.md`**, and **`gemcode/README.md`**.

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

