# Configuration

## Configuration sources
GemCode configuration is assembled from:
- CLI flags
- environment variables
- `.env` files
- project-local `.gemcode/` assets
- user-wide `~/.gemcode/` assets

Primary config model:
- `gemcode/src/gemcode/config.py`

## Project root
Almost every behavior is rooted at `project_root`.

It affects:
- allowed filesystem paths
- `.gemcode/` storage location
- instruction file loading
- skills, rules, styles, hooks, OpenAPI specs, MCP config

Use `-C` deliberately.

## Environment variables
The authoritative list is in:
- `gemcode/.env.example`

Important groups:

### Model routing
- `GEMCODE_MODEL`
- `GEMCODE_MODEL_MODE`
- `GEMCODE_MODEL_FAMILY_MODE`
- `GEMCODE_MODEL_DEEP_RESEARCH`
- `GEMCODE_MODEL_AUDIO_LIVE`
- `GEMCODE_MODEL_COMPUTER_USE`

### Capabilities
- `GEMCODE_ENABLE_DEEP_RESEARCH`
- `GEMCODE_ENABLE_EMBEDDINGS`
- `GEMCODE_ENABLE_MEMORY`
- `GEMCODE_ENABLE_COMPUTER_USE`
- `GEMCODE_ENABLE_AUDIO`
- `GEMCODE_ENABLE_MAPS_GROUNDING`

### Permissions and trust
- `GEMCODE_PERMISSION_MODE`
- `GEMCODE_INTERACTIVE_PERMISSION_ASK`
- `GEMCODE_TRUST_PROMPT`
- `GEMCODE_SUPER_MODE` — when `1`/`true`/`yes`/`on`, enables [super mode](tools-and-permissions.md#super-mode-fully-autonomous) (same idea as CLI `--super` and REPL `/super`): auto-approve GemCode tool gates, skip AFC stdin tool prompt, non-interactive `get_user_choice`, etc.

### UI and behavior
- `GEMCODE_TUI`
- `GEMCODE_OUTPUT_STYLE`
- `GEMCODE_AFC_PROMPT` — default **off** (`0`/`false`/unset): no interactive `afc>` prompt; all toolsets stay enabled when MCP/OpenAPI add non-callables (equivalent to approving “all tools”). Set to `1`/`true`/`on` to restore the stdin choice between all tools vs callable-only.
- `GEMCODE_AFC_DEFAULT` — when `GEMCODE_AFC_PROMPT=1` and set to `all` or `callables`, skips the interactive `afc>` prompt and preselects the tool mode when non-callable toolsets (MCP/OpenAPI) are present.
- `GEMCODE_TUI_WITH_KAIRA` — when `1`/`true`/`yes`/`on`, starts a headless **GemCode Runtime** inside the GemCode TUI process so background jobs stream inline (single-terminal mode). Env name is historical (“Kaira”).
- `GEMCODE_KAIRA_AUTO_CONNECT` — when `1`/`true`/`yes`/`on` (default), the GemCode TUI auto-connects to a running runtime using fleet socket discovery (see `GEMCODE_KAIRA_SOCKET` below) and streams job output inline.
- `GEMCODE_KAIRA_SOCKET` — optional **fallback** IPC path for clients when the fleet-default socket does not exist yet. GemCode resolves the manager socket in this order: `.gemcode/manager_ipc.txt` (written when `gemcode runtime` starts at the **fleet root**), then `<fleet_root>/.gemcode/ipc.sock` if present, then this env var if its path exists. **`gemcode runtime` no longer binds using this variable** (use `--socket` or the default path only) so a stale value in shell rc cannot hijack the daemon.
- `GEMCODE_ORG_BUS_REPORTS` — when `1`/`true`/`yes`/`on` (default), `org_delegate` emits `bus_message` events (`topic=org.report`) so multiple GemCode clients (and supervisors) can receive delegation results without scraping job logs. Finished/failed org reports are still written to `.gemcode/fleet_reports.jsonl` for the manager session when injection is on, even if this is `0`.
- **Fleet report inbox (manager session)** — completed `org.report` / `job.report` / `agent.report` outcomes (and formatted **`agent.dm`** / **`agent.broadcast`** lines) append to `.gemcode/fleet_reports.jsonl` at the **fleet root** (`resolve_fleet_root`). They are prepended to the next model turn when:
  - `GEMCODE_FLEET_REPORTS_INJECT=1` (default), and
  - the turn goes through `invoke.run_turn`, or the GemCode TUI (which applies the same drain).
  - Optional hands-off follow-up: **`GEMCODE_FLEET_REPORTS_AUTO_CONTINUE`** defaults to **on** (`1`): after each assistant response, if reports are still queued, GemCode runs extra digest turn(s) so the manager summarizes background work (habits, mesh jobs). Set to **`0`**/`false`/`off` to disable and only drain when you send the next message. Tune with `GEMCODE_FLEET_REPORTS_AUTO_CONTINUE_MODE=tui|enqueue|both`, `GEMCODE_FLEET_REPORTS_AUTO_CONTINUE_MAX` (default 3), `GEMCODE_FLEET_REPORTS_ENQUEUE_DEBOUNCE_S`, `GEMCODE_FLEET_REPORTS_MAX_CHARS`. See [`orchestration.md`](orchestration.md#fleet-report-inbox--auto-continue-hands-off-summaries).
  - **`GEMCODE_FLEET_TUI_NOTIFY`** — default **on** (`1`): when a mesh **`job.report`** completes, the GemCode TUI may print a short line (via `patch_stdout`) suggesting **`/fleet`** while you are idle at the prompt. Auto-continue does not run until after an assistant turn, so this bridges habits/mesh to the UI. Set **`0`**/`off` to disable. Throttle spacing with **`GEMCODE_FLEET_TUI_NOTIFY_MIN_S`** (default **8** seconds between hints).
- **`GEMCODE_MESH_WORKER_UNATTENDED`** — default **on** (`1`/`true`/unset): **Agent Mesh** jobs (habits, `org_delegate` queue, triggers) run with **`yes_to_all`** and **no interactive HITL** so shell / delegation / file tools do not block the main TUI. Set to **`0`**/`false`/`off` so mesh workers inherit the manager session’s `--yes` and HITL policy instead (stricter, but may stall background work until you approve in the same terminal).
- **Mesh SQLite session ordering** (always on, no env toggle): jobs that share the same agent durable ADK session (same `sessions.sqlite` path + `user_id` + `session_id`) run **`run_turn` one after another**, even when `GEMCODE_MESH_CONCURRENCY` is greater than 1. That avoids ADK **`SqliteSessionService`** optimistic-lock failures (“stale session” / `last_update_time`) when two habits or delegations for one org member overlap. Other agents still run in parallel. See [`orchestration.md`](orchestration.md#agent-mesh-in-process-orchestration).
- `GEMCODE_RUNTIME_MANAGER` — when `1`/`true`/`yes`/`on` (default), enables a minimal runtime manager loop that reacts to bus messages (e.g. `topic=org.assign` triggers an org delegation run; `topic=job.report` with `failed` triggers one automatic fix attempt).
- `GEMCODE_AGENT_HEARTBEAT_EVERY_S` — when set to a positive integer, the runtime publishes `topic=agent.heartbeat` periodically on the local bus. Useful for monitoring multi-agent setups.
- `GEMCODE_PARENT_SOCKET` — optional parent runtime IPC socket path. When set (typically in a child agent workspace), the child runtime will also publish `agent.heartbeat` to the parent runtime.
- `GEMCODE_AUTOMATIONS` — when `1`/`true`/`yes`/`on`, enables local scheduled automations from `.gemcode/automations/*.json` (executed by the **GemCode Runtime** daemon when running with `--automations`).
- `GEMCODE_KAIRA_HEARTBEAT_EVERY_S` — optional heartbeat interval (seconds) for the runtime (enqueues a heartbeat prompt repeatedly when automations are enabled).
- `GEMCODE_KAIRA_HEARTBEAT_PROMPT` — optional prompt text used by the heartbeat job.

### Agent instruction tuning
Built-in sections of the agent system prompt are assembled in `gemcode/src/gemcode/agent.py`; the parallel **tool system** manifest in `gemcode/src/gemcode/tool_prompt_manifest.py` stays aligned when present.

- **`GEMCODE_ENGINEERING_DISCIPLINE`** — Default **on** (unset or any value other than `0`, `false`, `no`, `off`). When **off**, GemCode omits the optional **Engineering discipline** block from the main instruction and the matching subsection from the tool manifest. When **on**, that block steers the model toward stating assumptions on ambiguous asks, the smallest adequate change, edits that match surrounding style without unrelated refactors, and a quick verification step before calling risky work “done.”

### Context and budgets
- **`GEMCODE_CONTEXT_CACHE`** — Default **on**. When **off** (`0`/`false`/`no`/`off`), disables ADK Gemini **context caching** (no server-side `cachedContents` reuse). Disabling avoids rare cleanup/API mismatch noise and slightly simplifies the request path, but **increases** repeated input tokens on long sessions when the prompt prefix is stable. Implemented in `gemcode/src/gemcode/session_runtime.py` (`ContextCacheConfig`).
- `GEMCODE_TOKEN_BUDGET`
- `GEMCODE_MAX_SESSION_TOKENS`
- compaction and policy variables from `.env.example`

## Project instruction files
GemCode loads project instructions in `gemcode/src/gemcode/agent.py`.

The current code supports:
- `gemcode.md`
- legacy instruction filenames (compatibility)
- ancestor and user-global variants

For operational accuracy, document and standardize around `gemcode.md` as the primary project instruction file.

## The `.gemcode/` directory

### Core state
- `sessions.sqlite`
- `sessions_meta.json`
- `audit.log`
- `fleet_reports.jsonl` (optional; fleet/agent completion inbox for the manager)
- `tool-results/`
- `artifacts/`
- `policy.json`

### Prompt assets
- `skills/`
- `output-styles/`
- `rules/`
- `hooks/`

### Agent fleet and workspaces
When you create agents with `/agent create`, GemCode persists the fleet registry and creates per-agent workspaces:

- `.gemcode/org.json` — fleet registry (members, hierarchy, bus addresses, workspace paths)
- `.gemcode/agents/<id>-<slug>/` — per-agent workspace

Inside each agent workspace, GemCode supports an optional constitution folder:
- `workspace/GOALS.md`
- `workspace/POLICIES.md`
- `workspace/SKILLS.md`
- `workspace/HEARTBEAT.md`
- `workspace/skills/*/SKILL.md`

When you run `gemcode -C .gemcode/agents/<id>-<slug>`, GemCode automatically loads and injects that `workspace/` content into the agent instruction.

### Integrations
- `mcp.json`
- `openapi/`
- `settings.json`

### Memory and notes
- `GEMCODE_MEMORY.md`
- `GEMCODE_USER.md`
- `memories.jsonl`
- `notes.md`
- `wal.jsonl`

## Rules and output styles

### Output styles
Locations:
- `.gemcode/output-styles/<name>.md`
- `~/.gemcode/output-styles/<name>.md`

### Rules
Locations:
- `.gemcode/rules/*.md`
- `~/.gemcode/rules/*.md`

Rules can include frontmatter path gating so they only apply to matching touched paths.

## GemSkills

### Skill locations
- `.gemcode/skills/<name>/SKILL.md`
- `~/.gemcode/skills/<name>/SKILL.md`

### Discovery behavior
GemCode preloads skill metadata and loads full bodies on demand.

Relevant code:
- `gemcode/src/gemcode/skills.py`
- `gemcode/src/gemcode/tools/skills.py`

### Frontmatter support
GemCode supports simple YAML-style frontmatter including:
- single-line scalars
- `description: >`
- `description: |`

## Hooks
Locations:
- `.gemcode/hooks/post_turn`
- `.gemcode/hooks/pre_tool_use`
- `.gemcode/hooks/post_tool_use`
- `.gemcode/hooks/session_start`
- `.gemcode/hooks/session_stop`

Hook logic:
- `gemcode/src/gemcode/hooks.py`
- plugin integration in `gemcode/src/gemcode/plugins/`

## MCP and OpenAPI

### MCP
Config file:
- `.gemcode/mcp.json`

Loader:
- `gemcode/src/gemcode/mcp_loader.py`

### OpenAPI
Spec directory:
- `.gemcode/openapi/`

Loader:
- `gemcode/src/gemcode/openapi_loader.py`

This is a first-class integration surface and should be documented alongside MCP, not as an afterthought.

## Settings and permission rules
Permission configuration can come from:
- `.gemcode/settings.json`
- `~/.gemcode/settings.json`

Permission evaluation lives in:
- `gemcode/src/gemcode/permissions.py`

This controls allow/deny patterns for tool execution, especially shell commands.

## User-wide state
GemCode also uses `~/.gemcode/` for:
- credentials
- trust metadata
- personal skills
- personal styles
- personal rules
- optional global instruction files

## Recommended configuration documentation practice
Treat these as separate layers:
1. environment and flags
2. project instruction files
3. `.gemcode/` assets
4. user-wide overrides

That separation is critical for production operators because it explains why behavior changes between repos, sessions, and machines.
