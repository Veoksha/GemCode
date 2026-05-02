# GemCode User Manual

This document is the primary user-facing manual for GemCode. It explains the product at a high level and points to the subsystem-specific documentation pages that provide production-grade depth.

## What GemCode is
GemCode is a local-first coding agent built around Google Gemini and the Google Agent Development Kit (ADK). It operates against a chosen project root and combines:
- a configuration model
- a runtime runner
- a root language-model agent
- a configurable tool inventory
- project-local state under `.gemcode/`

GemCode is designed for repository-native work rather than copy-paste chat workflows.

## Runtime modes

| Mode | Purpose |
|---|---|
| One-shot CLI | Single prompt/response runs |
| REPL | Stateful terminal interaction |
| TUI | GemCode terminal UI (scrollback-style; `tui/scrollback.py`) |
| IDE stdio | Editor integration over JSONL stdin/stdout |
| Agent Mesh | In-process multi-agent orchestration (automatic) |
| Kaira daemon | Optional always-on background scheduler |
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

### Run the scheduler
```bash
gemcode kaira -C .
```

### Orchestration (Agent Mesh + Multi-Agent)

GemCode includes a built-in multi-agent orchestration system that works automatically — no separate daemon required.

**Key features:**
- **Agent Mesh** — in-process concurrent agent execution with full tool access
- **Event Bus** — agents communicate via pub/sub (no Unix sockets needed)
- **Self-Triggering Agents** — agents auto-activate on events (e.g., verifier reviews completed work)
- **Delegation Learning** — remembers which agents succeed at which tasks
- **A2A Bridge** — expose/consume agents across machines via Google's A2A protocol

Quick example in the REPL:
```text
> Analyze the auth module. Delegate security review to the verifier.
```
The agent calls `org_delegate("verifier", ...)` → mesh runs a full-power verifier agent → result flows back automatically.

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
| **Self-improving skills** | When a delegation succeeds, the member's skill file gets a "Learned pattern" appended. Future invocations benefit from past successes. |
| **Proactive memory** | After exploring 5+ files or running 3+ commands, key discoveries are auto-saved to curated memory. Future sessions start with this knowledge. |
| **Progressive project map** | Every directory listing and file read updates `.gemcode/project_map.json`. The agent builds a map of your project over time. |
| **Auto-verification** | After 3+ file writes, the verifier agent auto-checks for syntax errors, broken imports, and logic bugs. |
| **Delegation suggestions** | `suggest_delegate(task)` recommends the best agent based on historical success patterns. |
| **Capability auto-enable** | If a project consistently uses web search or memory, those capabilities auto-enable in future sessions. |

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
| **Agent Mesh** | In-process multi-agent orchestration — each agent is a full GemCode session with own workspace, memory, and persistent history |
| **Agent Habits** | Scheduled recurring tasks (cron/interval/daily) — agents wake up and do work autonomously |
| **Self-Triggers** | Agents auto-activate on events (verification after changes, failure recovery) |
| **Self-Improving Skills** | Skills evolve — successful patterns are appended automatically |
| **Delegation Learning** | Remembers which agents succeed at which tasks, suggests optimal routing |
| **Progressive Learning** | Builds a project map as you navigate — future sessions skip discovery |
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
