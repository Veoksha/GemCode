## 0.4.28

- **Hosted workspace validate:** `/api/workspace/validate` rejects paths outside the tenant PVC root (project subfolders under the root remain allowed).

## 0.4.27

- **Web preview:** If nothing listens on static ports (8000/8080/…), auto-start `python -m http.server` in the workspace so hosted HTML apps open without a manual bash step. Nested paths (e.g. `/final todo/index.html`) are proxied correctly.

## 0.4.26

- **Web HITL resume:** Approve/deny no longer falsely reports `late` across the parent `gemcode serve` ↔ `sse_adapter` subprocess boundary. Uses `*.waiting` marker files so Yes continues the same SSE turn (CLI-style) instead of the UI injecting a new "Continue…" message.

## 0.4.25

- **Web HITL root fix:** Interactive web chat now **overrides** process-level `GEMCODE_SUPER_MODE`. Hosted tenants no longer silently auto-approve every tool while the model still invents "Approve / Deny" text. UI Auto-approve / `super_mode` is the source of truth per turn.
- **Prompt:** Permission instructions are dynamic — AUTO-APPROVE turns forbid mentioning approvals; interactive turns describe the inline Yes/No card only.
- **Deploy:** Tenant default `GEMCODE_SUPER_MODE=0` (mesh stays unattended via `GEMCODE_MESH_WORKER_UNATTENDED`). Set `=1` only if you want every web turn to skip cards.

## 0.4.24

- **Web HITL copy:** System prompt no longer tells the model about an "Approve dialog" — it describes the **inline Yes/No card** in chat so the agent stops asking users to approve a popup.
- **Preview proxy (hosted):** `GET /api/preview/proxy/{port}/…` reverse-proxies `127.0.0.1:{port}` on the tenant pod so the web UI can preview apps running inside GKE (not the user's laptop).

## 0.4.23

- **Web HITL reliability:** Confirmation handoffs in `sse_adapter`, `invoke`, and `kaira_daemon` respond only to confirmation FCs from the **last** ADK event in a batch (matches TUI). Fixes `Last response event should only contain the responses for the function calls in the same function call event` mid-turn crashes during repeated shell approvals.
- **UI chat persistence:** `GET`/`POST /api/ui/chat-store` stores the web UI conversation list on the workspace PVC (`.gemcode/ui_chat_store.json`). POST handler args corrected for hosted tenants.
- **Habit chains:** Habits can fire when another habit completes (`trigger_after` / `trigger_on`) via `habit_chains.py`. Cycle detection on add. Env: `GEMCODE_HABIT_CHAINS` (default on).
- **Habit run history:** Per-habit run records (`habit_runs.py`) and web API `POST /api/habits` action `runs` for the Agents panel.
- **Hosted trust:** `ensure_hosted_workspace_trust` auto-trusts `GEMCODE_HOSTED_TENANT_ROOT` for chat turns and tenant entrypoint (no interactive trust prompt on GKE).
- **Model aliases:** Web `gemcode-fast` maps to `gemini-3.1-flash-lite`.
- **Deploy:** Gateway/tenant Cloud Build configs, hosted auth/troubleshooting docs, Vercel + tunnel helper scripts.
- **Docs:** [`web-ui-contract.md`](../docs/web-ui-contract.md) chat-store + habit runs; [`orchestration.md`](../docs/orchestration.md) habit chains; [`hosted.md`](../docs/hosted.md) 0.4.23.

## 0.4.22

- **Hosted file API:** `GET /api/files`, `GET /api/files/read`, `POST /api/files/write` on `gemcode serve` — workspace tree, read, and write with `GEMCODE_HOSTED_TENANT_ROOT` path locking (enables remote web UI file explorer per tenant).
- **Web API default model:** `sse_adapter` fallback aligned with CLI — `gemini-3.1-pro-preview` when the client omits `model` (per-request UI model override unchanged).
- **REPL:** `/workspace` slash — workspace root, hosted lock, file API routes.
- **Deploy:** GKE network policy DNS egress fix, `GEMCODE_MODEL` on tenant pods, Cloud Build config, private cluster setup script updates.
- **Docs:** [`hosted.md`](../docs/hosted.md) architecture for full superpowers + isolation; [`web-ui-contract.md`](../docs/web-ui-contract.md) file routes.

## 0.4.21

- **Hosted multi-tenant web API:** `GEMCODE_HOSTED_TENANT_ROOT` locks `gemcode serve` to one workspace per process — client `path` / `project_root` cannot escape the tenant directory (HTTP 403). HITL approval files live under `{workspace}/.gemcode/web_approvals` when hosted mode is on.
- **REPL:** `/hosted` slash command — show hosted-tenant lock status and env hints.
- **Web API:** `/api/health` includes `hosted_mode` and `hosted_tenant_root` when locked.
- **Docs:** [`hosted.md`](../docs/hosted.md) — GKE pod-per-user deployment guide (`deploy/gcp/`). Updated [`configuration.md`](../docs/configuration.md), [`web-ui-contract.md`](../docs/web-ui-contract.md), [`cli-and-repl.md`](../docs/cli-and-repl.md).

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

