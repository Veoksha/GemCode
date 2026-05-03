# GemCode User Manual

This document is the primary user-facing manual for GemCode. It explains the product at a high level and points to the subsystem-specific documentation pages that provide production-grade depth.

## What GemCode is
GemCode is a local-first, self-evolving coding agent built on Google Gemini and the Agent Development Kit (ADK). It operates against a chosen project root and provides:

- **Autonomous multi-agent orchestration** — agents delegate, verify, and fix each other's work
- **Self-healing code** — changes are auto-verified; failures are auto-fixed
- **Self-evolving tools** — the agent creates new reusable tools from repeated patterns
- **Progressive learning** — gets smarter with every session (memory, skills, project map)
- **Scheduled habits** — agents wake up on cron/interval to run tests, audits, checks
- **Cross-machine agents** — expose/consume agents via Google A2A protocol
- **58 built-in tools** — filesystem, shell, web, search, memory, orchestration, synthesis
- **Mesh worker sessions** add `agent_dm` / `agent_broadcast` (bus + `fleet_reports.jsonl` inbox formatting)
- **Full ADK integration** — native sub-agents, transfer_to_agent, output_key, workflow agents

All state lives under `.gemcode/` in the project root. No external services required beyond a Gemini API key.

## Runtime modes

| Mode | Purpose |
|---|---|
| One-shot CLI | Single prompt/response runs |
| REPL | Stateful terminal interaction |
| TUI | GemCode terminal UI (scrollback-style; `tui/scrollback.py`) |
| IDE stdio | Editor integration over JSONL stdin/stdout |
| Agent Mesh | In-process multi-agent orchestration (automatic) |
| GemCode Runtime | Optional always-on background scheduler (`gemcode runtime`; alias `gemcode kaira`) |
| A2A server | Cross-machine agent communication via Google A2A protocol |
| Live audio (experimental) | Microphone-driven Gemini Live sessions |

## Recommended reading order

### 1. Setup and first use
- [`../docs/install.md`](../docs/install.md)

### 2. Interactive use
- [`../docs/cli-and-repl.md`](../docs/cli-and-repl.md)

### 3. Configuration and local assets
- [`../docs/configuration.md`](../docs/configuration.md)

### 4. Tooling and safety model
- [`../docs/tools-and-permissions.md`](../docs/tools-and-permissions.md)

### 5. Optional capability bundles
- [`../docs/capabilities.md`](../docs/capabilities.md)

### 6. Integrations
- [`../docs/integrations.md`](../docs/integrations.md)
- [`../docs/web-ui-contract.md`](../docs/web-ui-contract.md)

### 7. Operations and release
- [`../docs/operations.md`](../docs/operations.md)

### 8. `.gemcode/` state reference
- [`../docs/reference-gemcode-state.md`](../docs/reference-gemcode-state.md)

### 9. Architecture deep dive
- [`../docs/architecture.md`](../docs/architecture.md)

## Quickstart

### Install
```bash
cd gemcode
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
```

### Set your API key
```bash
export GOOGLE_API_KEY="your-key"
```

### Start GemCode against a project
```bash
gemcode -C /path/to/project
```

### One-shot run
```bash
gemcode -C /path/to/project "Explain this repository"
```

### Mutating run
```bash
gemcode -C /path/to/project --yes "Fix the failing tests"
```

## Essential concepts

### Project root
Every GemCode run is anchored to a project root. This determines:
- what files are visible
- where `.gemcode/` state is stored
- what instruction files are loaded
- which repo-local assets are active

### Multi-agent habits, skills, and mesh runtime

- **Habits** for the whole fleet live in **one** file: `.gemcode/habits.json` at the **fleet root**. Each row names which **org member** runs (`agent` field), on what schedule—different members can have different prompts and intervals at once.
- **Skills** and **per-turn runtime** are **per member**: org `skill_name`, member skill under `.gemcode/skills/`, and optional **agent workspace** `.gemcode/agents/<id>-<slug>/` (own session DB, memory, local skills). Mesh jobs use that context automatically.
- **Stopping automation:** removing a habit only stops *new* work. To cancel **queued** or **running** mesh jobs, use **`/mesh halt`** or the **`mesh_halt`** tool (see [`orchestration.md`](../docs/orchestration.md#stopping-background-work-habits-removed-but-jobs-still-finishing)).

### `.gemcode/`
GemCode stores project-local state under `.gemcode/`, including:
- sessions
- logs
- artifacts
- memory
- skills
- rules
- output styles
- hooks
- integration config

Reference:
- [`../docs/reference-gemcode-state.md`](../docs/reference-gemcode-state.md)

### Project instruction files
GemCode supports project instruction files loaded by the agent layer. The live code treats `gemcode.md` as the primary project instruction file and also supports legacy instruction filenames for compatibility.

Reference:
- [`../docs/configuration.md`](../docs/configuration.md)

### GemSkills
GemSkills are reusable prompt playbooks stored under:
- `.gemcode/skills/<name>/SKILL.md`
- `~/.gemcode/skills/<name>/SKILL.md`

They support:
- creation
- session loading
- one-shot invocation
- iterative editing

### Permissions
GemCode combines:
- workspace trust
- permission mode
- allow/deny settings
- blanket approval flags
- interactive approval prompts

Reference:
- [`../docs/tools-and-permissions.md`](../docs/tools-and-permissions.md)

### Super mode (fully autonomous)

Use when you want GemCode to run with all powers unlocked and zero friction. In super mode:
- All capabilities auto-enable (memory, web search, agents, habits, triggers)
- Default org members auto-create (kaira + verifier)
- Habits auto-generate based on project type
- Auto-verification runs after risky changes
- No confirmation prompts for any operation

- **CLI:** `gemcode -C . --super "your task"`
- **Env:** `GEMCODE_SUPER_MODE=1`
- **REPL/TUI:** `/super` (use `/super off` to clear)

In normal mode, GemCode asks on first run: "Enable autonomous mode? [Y/n]"

Details: [`../docs/orchestration.md`](../docs/orchestration.md).

## Common commands

### Inspect models
```bash
gemcode models
```

### Start the REPL
```bash
gemcode -C .
```

### Attach a file to a one-shot turn
```bash
gemcode -C . --attach ./report.pdf "Summarize this"
```

### Run the scheduler (background jobs + optional automations)
```bash
gemcode runtime -C .
# alias:
gemcode kaira -C .
```

### Orchestration (Agent Mesh + Multi-Agent)

GemCode includes a built-in multi-agent orchestration system that works automatically in-process (**Agent Mesh**). A separate **`gemcode runtime`** process is optional: use it for a dedicated job queue, IPC attach, and `.gemcode/automations/` schedules.

**Key features:**
- **Native ADK sub-agents** — org members are real ADK sub-agents with `transfer_to_agent` routing
- **Agent Mesh** — async background execution with full GemCode sessions per agent
- **Event Bus** — agents communicate via pub/sub (no Unix sockets needed)
- **Self-Healing** — closed loop: change → verify → fix → verify → done
- **Self-Triggers** — agents auto-activate on events (verification, failure recovery)
- **Tool Synthesis** — agent creates new reusable tools from repeated patterns
- **Delegation Learning** — remembers which agents succeed at which tasks
- **A2A Bridge** — expose/consume agents across machines via Google A2A protocol

Quick example in the REPL:
```text
> Analyze the auth module. Delegate security review to the verifier.
```
The LLM calls `transfer_to_agent(agent_name='verifier')` → ADK routes natively → verifier runs → result saved to session state.

For background work: `org_delegate("kaira", "run tests")` → mesh runs kaira as a full GemCode session → result flows back via fleet reports.

**Mesh / habits reliability:** overlapping jobs for the **same** org member serialize writes to that agent’s durable SQLite session so ADK does not raise “stale session” errors. Mesh workers default to **unattended tool approval** (env **`GEMCODE_MESH_WORKER_UNATTENDED`**, on by default) so background shell / delegation / writes do not block the main TUI on HITL. Fleet auto-continue digests the inbox after **assistant** turns; while idle at ❯, use **`/fleet`** / **`/fleet show`** or any message — see **`GEMCODE_FLEET_TUI_NOTIFY`** in [`../docs/configuration.md`](../docs/configuration.md). See [`../docs/orchestration.md`](../docs/orchestration.md).

Docs:
- [`../docs/orchestration.md`](../docs/orchestration.md)

### Start the IDE bridge
```bash
gemcode ide --stdio
```

### Run live audio
```bash
gemcode live-audio -C .
```

Status note:
- `live-audio` is currently **experimental** and may fail due to upstream Gemini Live availability/reliability (for example transient `1011` internal errors).
- Treat this as **future scope** for production workflows.

## REPL command highlights

| Command | Purpose |
|---|---|
| `/help` | Command summary |
| `/status` | Model, capabilities, context, and runtime telemetry |
| `/context` | Context pressure and prompt budget telemetry |
| `/cost` | Token and cost estimate summary |
| `/attach` | Queue file attachments for the next turn |
| `/trust` | Manage workspace trust |
| `/init` | Generate project instructions |
| `/skills` | List skills |
| `/gemskill` | Load a skill into the session prompt |
| `/style` | Set session output style |
| `/rules` | Inspect active rules |
| `/diff` | Show current diff/checkpoint diff |
| `/rewind` | Restore checkpoints |
| `/review` | Run a parallel code review pipeline |
| `/eval` | Run evaluation gates |
| `/kaira` | Show scheduler usage help |
| `/super` | Super mode: auto-approve tools, no GemCode HITL · `/super off` |

## Orchestration commands

| Command | Purpose |
|---|---|
| `/agent list` | Show all org members |
| `/agent tree` | Show org hierarchy |
| `/agent create` | Create a new agent member |
| `/agent assign <member> <task>` | Delegate work to a member |
| `/agent improve <member> <lessons>` | Improve a member's skill |

## Intelligence features (automatic)

These work without configuration. In super mode, everything is enabled silently. In normal mode, GemCode asks once on first run.

| Feature | How it works |
|---|---|
| **Codebase awareness** | Builds a persistent understanding of the project from every tool call — file structure, imports/exports, recent changes, learned facts. The agent starts each turn already knowing the project. |
| **Self-healing** | After file changes, auto-runs tests/lint. If they fail, auto-fixes (up to 2 attempts). Closed loop: change → verify → fix → verify → done. |
| **Tool synthesis** | When the agent repeats multi-step operations, it can create a reusable tool (bash/python script) stored in `.gemcode/synthesized_tools/`. |
| **Self-improving skills** | When a delegation succeeds, the member's skill file gets a "Learned pattern" appended. Future invocations benefit from past successes. |
| **Proactive memory** | After exploring 5+ files or running 3+ commands, key discoveries are auto-saved to curated memory. Future sessions start with this knowledge. |
| **Impact analysis** | When a file changes, GemCode knows which other files are affected (via import tracking + learned correlations). Self-healing runs only relevant tests. |
| **Auto-verification** | After 3+ file writes, the verifier agent auto-checks for syntax errors, broken imports, and logic bugs. |
| **Delegation suggestions** | `suggest_delegate(task)` recommends the best agent based on historical success patterns. |
| **Capability auto-enable** | If a project consistently uses web search or memory, those capabilities auto-enable in future sessions. |

## Tool Synthesis (self-evolving)

The agent can create new reusable tools when it detects repeated patterns:

```text
# Create a tool
synthesize_tool("run-tests", "Run pytest with coverage", "pytest --cov=src -q")
synthesize_tool("deploy-staging", "Deploy to staging", "git push origin main && ssh staging 'cd app && git pull'")

# Use it later
run_synthesized_tool("run-tests")
run_synthesized_tool("deploy-staging")

# List all synthesized tools
list_synthesized_tools()
```

Tools persist in `.gemcode/synthesized_tools/` across sessions.

## Agent Habits (scheduled tasks)

Agents can run recurring tasks on a schedule — no daemon needed, runs inside the main GemCode process.

```text
# From the agent (tools):
habits_add("test-watch", "kaira", "Run pytest -q and report", every_minutes=30)
habits_add("nightly-audit", "verifier", "Full security review", daily_at="02:00")
habits_add("hourly-status", "self", "Summarize recent changes", cron="0 * * * *")

# Management:
habits_list()
habits_pause("test-watch")
habits_resume("test-watch")
habits_remove("test-watch")
```

In super mode, GemCode auto-creates habits based on project type (test-watch for Python, lint-watch for Node).

Detailed behavior:
- [`../docs/cli-and-repl.md`](../docs/cli-and-repl.md)

## Capability overview

| Capability | What it adds |
|---|---|
| **Codebase Awareness** | Persistent understanding of the project — structure graph, change journal, insight cache. Compounds over time, zero extra cost |
| **Agent Mesh** | In-process multi-agent orchestration — each agent is a full GemCode session with own workspace, memory, and persistent history |
| **Self-Healing** | Closed loop: change → verify → fix → verify → done. Code repairs itself automatically |
| **Tool Synthesis** | Agent creates new reusable tools at runtime from repeated patterns |
| **Agent Habits** | Scheduled recurring tasks (cron/interval/daily) — agents wake up and do work autonomously |
| **Self-Triggers** | Agents auto-activate on events (verification after changes, failure recovery) |
| **Self-Improving Skills** | Skills evolve — successful patterns are appended automatically |
| **Delegation Learning** | Remembers which agents succeed at which tasks, suggests optimal routing |
| **Proactive Memory** | Auto-saves important discoveries to curated memory without being asked |
| **A2A Bridge** | Cross-machine agent communication via Google A2A protocol |
| **Event Bus** | In-memory pub/sub for agent-to-agent communication |
| **Deep research** | Research-focused tool routing and optional dedicated model path |
| **Embeddings** | Semantic search and optional embedding-backed memory |
| **Memory** | Retrieval-oriented persistent memory across sessions |
| **Browser/computer use** | Playwright-backed browser automation and inspection |
| **Checkpoints** | File mutations are reversible — undo any agent edit |
| **Live audio** | Gemini Live microphone sessions (experimental) |

Detailed behavior:
- [`../docs/capabilities.md`](../docs/capabilities.md)

## Integrations overview

| Integration | Entry point |
|---|---|
| IDE bridge | `gemcode ide --stdio` |
| Web/SSE | documented in `docs/web-ui-contract.md` |
| MCP | `.gemcode/mcp.json` |
| OpenAPI | `.gemcode/openapi/` |

Detailed behavior:
- [`../docs/integrations.md`](../docs/integrations.md)

## Release and maintenance
Package version lives in:
- `gemcode/pyproject.toml`

Release operations, troubleshooting, and PyPI workflow are documented in:
- [`../docs/operations.md`](../docs/operations.md)

## Documentation policy
This manual is intentionally concise. The detailed production documentation lives under `docs/` and is organized by subsystem and operator concern so it can stay accurate as GemCode evolves.
