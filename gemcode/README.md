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
| TUI | Scrollback terminal UI over the REPL runtime |
| IDE stdio | Editor integration over JSONL stdin/stdout |
| Kaira | Priority-queue scheduler for background jobs |
| Live audio (future scope) | Planned: microphone-driven Gemini Live sessions (currently experimental/unreliable) |

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
GemCode supports project instruction files loaded by the agent layer. The live code treats `gemcode.md` as the primary project instruction file and supports `GEMINI.md` as a compatibility path.

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
| `/review` | Run a review workflow |
| `/eval` | Run evaluation gates |
| `/kaira` | Show scheduler usage help |

Detailed behavior:
- [`../docs/cli-and-repl.md`](../docs/cli-and-repl.md)

## Capability overview

| Capability | What it adds |
|---|---|
| Deep research | research-focused tool routing and optional dedicated model path |
| Embeddings | semantic search and optional embedding-backed memory |
| Memory | retrieval-oriented persistent memory |
| Browser/computer use | Playwright-backed browser automation and inspection |
| Live audio | Gemini Live microphone sessions |

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
