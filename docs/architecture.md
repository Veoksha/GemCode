# GemCode Architecture

## Overview
GemCode is a local-first coding agent built around Google Gemini and the Google Agent Development Kit (ADK). The system is organized around one core pipeline:

1. Build a `GemCodeConfig`
2. Assemble a runtime `Runner`
3. Build the root `LlmAgent` instruction and tool inventory
4. Execute one or more turns
5. Persist session state, artifacts, logs, and optional memory under `.gemcode/`

The composition root for that pipeline is `gemcode/src/gemcode/session_runtime.py`.

## Major subsystems

### CLI and mode dispatch
- `gemcode/src/gemcode/cli.py`
- `gemcode/src/gemcode/__main__.py`

This layer parses commands and flags, loads environment configuration, selects the runtime mode, and enters one of these flows:
- One-shot CLI prompt
- Interactive REPL (line-based)
- GemCode TUI (scrollback-style terminal UI when `GEMCODE_TUI=1`)
- IDE stdio bridge
- GemCode Runtime daemon (`gemcode runtime`; alias `gemcode kaira`)
- Live audio (experimental / future scope)

### Configuration and routing
- `gemcode/src/gemcode/config.py`
- `gemcode/src/gemcode/model_routing.py`
- `gemcode/src/gemcode/capability_routing.py`

This layer defines the configuration model, environment-variable defaults, capability toggles, and per-turn model selection heuristics.

### Orchestration and intelligence
- `gemcode/src/gemcode/agent_mesh.py` — Multi-agent orchestration (scheduler in a **background thread** + dedicated asyncio loop; bus + fleet reports)
- `gemcode/src/gemcode/event_bus.py` — In-memory pub/sub for agent communication
- `gemcode/src/gemcode/agent_intelligence.py` — Pre/post-turn learning and structural decisions
- `gemcode/src/gemcode/agent_triggers.py` — Self-triggering agents (event-driven activation)
- `gemcode/src/gemcode/agent_habits.py` — Scheduled recurring tasks (cron/interval/daily)
- `gemcode/src/gemcode/delegation_learning.py` — Delegation outcome memory
- `gemcode/src/gemcode/self_healing.py` — Auto-verify and auto-fix after changes
- `gemcode/src/gemcode/tool_synthesis.py` — Agent-created reusable tools
- `gemcode/src/gemcode/a2a_bridge.py` — Cross-machine agent communication (Google A2A)
- `gemcode/src/gemcode/org.py` — Agent fleet registry
- `gemcode/src/gemcode/fleet_reports.py` — Background agent result persistence

This layer provides autonomous multi-agent behavior:
- Org members become native ADK `sub_agents` with `transfer_to_agent` routing
- The Agent Mesh runs background jobs as full GemCode sessions (own workspace, memory, history)
- The Event Bus enables agent-to-agent communication without Unix sockets
- Self-healing auto-detects verification commands and fixes failures
- Tool synthesis creates reusable scripts from repeated patterns
- Delegation learning remembers which agents succeed at which tasks
- Habits run scheduled tasks autonomously in-process (optional `gemcode runtime` for `.gemcode/automations/` and a dedicated job queue)
- Triggers auto-activate agents on bus events

### Runtime assembly
- `gemcode/src/gemcode/session_runtime.py`

This is the composition root. It builds:
- the ADK `Runner`
- SQLite-backed session storage
- the root `LlmAgent`
- optional artifact and memory services
- plugins
- modality, browser, MCP, OpenAPI, and other external tool surfaces

### Agent instruction construction
- `gemcode/src/gemcode/agent.py`
- `gemcode/src/gemcode/tool_prompt_manifest.py`
- `gemcode/src/gemcode/output_styles.py`
- `gemcode/src/gemcode/rules.py`
- `gemcode/src/gemcode/skills.py`

This layer constructs the effective instruction seen by the model. It merges:
- runtime facts
- optional engineering discipline guidance (toggle `GEMCODE_ENGINEERING_DISCIPLINE`; see [`configuration.md`](configuration.md#agent-instruction-tuning))
- project instruction files
- tool manifest text
- loaded rules
- active output style
- skill manifest metadata
- session-loaded skill bodies
- optional curated memory and wake-up context

### Turn execution
- `gemcode/src/gemcode/invoke.py`
- `gemcode/src/gemcode/multimodal_input.py`

This layer handles one user turn end-to-end:
- build the user content payload
- attach files
- run the ADK session
- process tool calls and confirmations
- apply retry and compaction logic
- return final text and telemetry

### Tool system
- `gemcode/src/gemcode/tools/__init__.py`
- `gemcode/src/gemcode/tools/`
- `gemcode/src/gemcode/modality_tools.py`
- `gemcode/src/gemcode/tools/skills.py`

GemCode merges multiple tool surfaces:
- Python callables
- ADK built-in tools
- ADK toolsets
- browser/computer-use toolsets
- MCP toolsets
- OpenAPI-generated toolsets
- skill loader helpers

### Interactive surfaces
- `gemcode/src/gemcode/repl_slash.py`
- `gemcode/src/gemcode/repl_commands.py`
- `gemcode/src/gemcode/tui/scrollback.py`
- `gemcode/src/gemcode/tui/input_handler.py`
- `gemcode/src/gemcode/ide_stdio.py`
- `gemcode/src/gemcode/kaira_daemon.py`
- `gemcode/src/gemcode/kaira_ipc.py`
- `gemcode/src/gemcode/kaira_client.py`
- `gemcode/src/gemcode/kaira_job_store.py`
- `gemcode/src/gemcode/org.py`
- `gemcode/src/gemcode/tools/org_tools.py`
- `gemcode/src/gemcode/fleet_reports.py` — durable inbox (`.gemcode/fleet_reports.jsonl`) + optional auto-continue
- `gemcode/src/gemcode/tools/user_choice.py` — `get_user_choice` override in super mode
- `gemcode/src/gemcode/live_audio_engine.py`

These provide different UX layers over the same core runner architecture.

## Runtime flows

### One-shot CLI
Entry: `gemcode "prompt"`

Flow:
1. Parse flags in `cli.py`
2. Build `GemCodeConfig`
3. Apply capability routing
4. Pick the effective model
5. Call `create_runner()`
6. Call `run_turn()`
7. Print final text output
8. Close the runner

This is the cleanest path for scripts and one-off questions.

### REPL and TUI
Entry: `gemcode` with no prompt argument in a TTY

Flow:
1. Build one long-lived runner
2. Read lines from the user (plain REPL) or the GemCode TUI input layer (`tui/input_handler.py`)
3. Process slash commands in `repl_slash.py`
4. Apply capability and model routing per turn
5. Execute the model turn:
   - **Plain REPL** (`GEMCODE_TUI=0`): `invoke.run_turn()` (drains `.gemcode/fleet_reports.jsonl` into the prompt when enabled)
   - **GemCode TUI** (`GEMCODE_TUI=1`, default in TTY): `tui/scrollback.py` drives `runner.run_async` directly and prepends the same fleet inbox drain before each turn
6. Reuse the same session id until exit or session reset

If terminal support is available and `GEMCODE_TUI` is enabled, GemCode uses the **only** shipped terminal UI: `gemcode/src/gemcode/tui/scrollback.py` (scrollback-style, not a separate fullscreen app).

### IDE stdio bridge
Entry: `gemcode ide --stdio`

Flow:
1. Read JSONL requests from stdin
2. Materialize inline attachments into temp files
3. Lazily create a runner
4. Route requests through the normal turn pipeline
5. Emit JSONL responses, tool proposals, and suggestions

In IDE mode, mutating shell/file tools may emit proposals instead of directly changing the filesystem.

### GemCode Runtime daemon
Entry: `gemcode runtime` (alias: `gemcode kaira`)

Flow:
1. Start a background queue
2. Read prompts from stdin
3. Enqueue prompts with priority
4. Spawn isolated job executions with fresh runners
5. Stream job status/results back to the terminal

The runtime is a scheduler, not a TUI shell. It is optimized for queued background jobs rather than scrollback interaction.

Operational note:
- The runtime also exposes a **Unix-socket JSONL IPC control plane** and **event stream**, so the TUI/REPL can subscribe and control jobs.
- Job records are persisted under `.gemcode/kaira/jobs/` so status survives restarts.

Related docs:
- `docs/orchestration.md`

### Live audio
Entry: `gemcode live-audio`

Flow:
1. Build the same core runner stack
2. Capture microphone audio
3. Send audio via Gemini Live / ADK live flow
4. Return streamed model output

This path shares configuration, tool loading, and agent assembly with the text-based runtime.

Operational note:
- This mode is currently **experimental** and depends on upstream Gemini Live availability/reliability. Treat it as **future scope** for production.

## Runner assembly in detail
`create_runner()` in `gemcode/src/gemcode/session_runtime.py` performs most system wiring:

1. Load policy calibration profile
2. Merge modality tools
3. Load curated memory snapshot
4. Optionally load VeoMem wake-up context
5. Load MCP toolsets from `.gemcode/mcp.json`
6. Load OpenAPI toolsets from `.gemcode/openapi/`
7. Optionally add computer-use and browser tools
8. Build the root agent
9. Create the SQLite session service
10. Attach plugins
11. Attach optional memory services
12. Attach artifact service
13. Return the ADK `Runner`

## Instruction hierarchy
The effective model instruction is not a single static prompt. It is assembled from multiple layers:

- global behavior
- runtime facts
- optional engineering discipline block (same toggle as `GEMCODE_ENGINEERING_DISCIPLINE` in [`configuration.md`](configuration.md#agent-instruction-tuning))
- project instruction files
- notes and curated memory
- active capabilities
- tool manifest text
- output style
- rules
- skill manifest metadata
- session-loaded skill bodies

Project instruction files are loaded from a hierarchy in `gemcode/src/gemcode/agent.py`. The code treats `gemcode.md` as the primary project instruction file and supports legacy instruction filenames for compatibility.

## Tool-loading surfaces

### Core callable tools
These live under `gemcode/src/gemcode/tools/` and are registered by `gemcode/src/gemcode/tools/__init__.py`.

Families include:
- planning and todos
- filesystem and search
- mutations
- shell and background processes
- notebooks
- web access
- checkpoints
- curated memory
- skill discovery/loading
- subtasks

### Modality tools
`gemcode/src/gemcode/modality_tools.py` can add:
- web search built-ins
- URL context tools
- Maps grounding
- semantic search

### Browser and computer use
If enabled, `session_runtime.py` adds:
- Playwright-backed computer-use toolsets
- read-only browser inspection helpers

### MCP toolsets
Loaded from `.gemcode/mcp.json` by `gemcode/src/gemcode/mcp_loader.py`.

### OpenAPI toolsets
Loaded from `.gemcode/openapi/` by `gemcode/src/gemcode/openapi_loader.py`.

### Skill tools
`gemcode/src/gemcode/skills.py` and `gemcode/src/gemcode/tools/skills.py` expose GemSkills as:
- metadata discovery
- one-shot expansion
- session-pinned prompt content

## Persistence and state

### Session storage
- `.gemcode/sessions.sqlite`
- `.gemcode/sessions_meta.json`

### Tool artifacts and offload
- `.gemcode/artifacts/`
- `.gemcode/tool-results/`
- `.gemcode/audit.log`
- `.gemcode/fleet_reports.jsonl` — inbox for completed `org.report` / `job.report` / `agent.report` plus **`agent.dm`** / **`agent.broadcast`** lines (drained into the next manager turn when `GEMCODE_FLEET_REPORTS_INJECT=1`)
- `.gemcode/debug.yaml` when debug logging is enabled

### Memory layers
- `.gemcode/GEMCODE_MEMORY.md`
- `.gemcode/GEMCODE_USER.md`
- `.gemcode/memories.jsonl`

### Policy and evaluation
- `.gemcode/policy.json`
- `.gemcode/evals/last_eval.json`
- `.gemcode/evals/autotune_ledger.jsonl`

### Prompt assets
- `.gemcode/skills/`
- `.gemcode/output-styles/`
- `.gemcode/rules/`
- `.gemcode/hooks/`
- `.gemcode/openapi/`
- `.gemcode/mcp.json`
- `.gemcode/settings.json`

## Model and capability routing

### Capability routing
`gemcode/src/gemcode/capability_routing.py` enables capability bundles based on:
- explicit flags
- environment variables
- prompt heuristics

Capabilities include:
- deep research
- embeddings
- memory
- computer use
- audio

### Model routing
`gemcode/src/gemcode/model_routing.py` selects the effective model per turn.

Priority order:
1. explicit model override
2. deep-research model
3. audio model
4. computer-use model
5. routed default across fast, balanced, quality and primary/alt families

## Plugins and callbacks
Important plugin surfaces:
- `gemcode/src/gemcode/plugins/tool_recovery_plugin.py`
- `gemcode/src/gemcode/plugins/terminal_hooks_plugin.py`
- global instruction plugin wiring in `session_runtime.py`

Callback assembly and tool summaries live in `gemcode/src/gemcode/callbacks.py`.

## Operational implications

### Why docs must separate user modes
The same agent/runtime core powers multiple user experiences, but they behave differently:
- CLI is synchronous and disposable
- REPL/TUI is stateful and session-oriented
- IDE mode is proposal-oriented
- Kaira is queue-oriented
- live audio is stream-oriented

Good documentation must describe each as a first-class operating mode rather than treating them as minor variations.

### Why docs must separate tool families
GemCode mixes several incompatible tool representations:
- callables
- ADK built-ins
- toolsets
- MCP/OpenAPI-generated tools

This matters operationally because behavior such as Automatic Function Calling can differ depending on which tool surfaces are active.

## Recommended reading order
1. `docs/install.md`
2. `docs/cli-and-repl.md`
3. `docs/configuration.md`
4. `docs/tools-and-permissions.md`
5. `docs/capabilities.md`
6. `docs/integrations.md`
7. `docs/operations.md`
8. `docs/reference-gemcode-state.md`
