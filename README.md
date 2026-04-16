# GemCode

![PyPI](https://img.shields.io/pypi/v/gemcode?label=PyPI&style=flat)
![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)

GemCode is a local-first coding agent for real repositories, built on Google Gemini and the Google Agent Development Kit (ADK). It runs against your project directory, orchestrates tool calls, and keeps project-local state under `.gemcode/` so sessions, skills, logs, and runtime artifacts stay tied to the codebase you are actually working on.

It is built for repository-native work rather than copy-paste chat: reading files, editing code, searching symbols, running controlled shell commands, loading reusable skills, and operating with explicit trust and permission controls.

GemCode is an agentic coding tool that lives in your terminal. It understands your codebase, helps you code faster by executing routine repo tasks, explaining complex code in context, and inspecting changes via diffs and checkpoints, all behind explicit trust and permission gates.

- Operates from a chosen project root (`-C`) with persistent state under `.gemcode/`
- Supports inspection-first iteration (`/diff`, checkpoints, and audit logs)
- Offers reusable workflows via GemSkills
- Works across multiple entry points: CLI/REPL/TUI, IDE stdio bridge, Kaira, and live audio

## Built for repo-first work

Unlike chat-first tools, GemCode is optimized for codebase-first workflows:

- it operates against a chosen project root
- it keeps inspectable local state
- it supports persistent sessions
- it exposes structured tools instead of relying only on raw text
- it can be extended with project-local instructions, rules, styles, hooks, and GemSkills
- it supports both interactive and automation-oriented entry points

## What GemCode provides

- **Repository-aware execution**: read, search, edit, diff, and run shell commands against real files
- **Local project state**: `.gemcode/` stores sessions, logs, artifacts, skills, rules, styles, eval outputs, and integration config
- **Safety controls**: workspace trust, permission modes, interactive approvals, and command gating
- **GemSkills**: reusable markdown playbooks stored under `.gemcode/skills/<name>/SKILL.md`
- **Context and token controls**: budgeting, context telemetry, tool-result offloading, and compaction-aware runtime behavior
- **Multiple user interfaces**: one-shot CLI, REPL, TUI, IDE stdio bridge, Kaira scheduler, and live audio
- **Integration surfaces**: MCP, OpenAPI, web-compatible contracts, optional browser/computer-use flows, and memory systems

## Runtime model

At a high level, a GemCode run looks like this:

1. Build a `GemCodeConfig`
2. Resolve the active project root
3. Assemble the runtime runner and tool inventory
4. Build the root agent instruction from config + local assets
5. Execute turns against Gemini and tools
6. Persist state under `.gemcode/`

That means GemCode is not just a single prompt wrapper. It is a runtime that combines:

- a configuration model
- a session store
- an agent instruction builder
- a tool-loading pipeline
- project-local extension assets
- multiple execution frontends

## Installation

### 60-second quickstart

1. Install:
```bash
pip install gemcode
```

2. Set your Gemini API key:
```bash
export GOOGLE_API_KEY="your-key"
```

GemCode reads `GOOGLE_API_KEY` from your environment (or a `.env` file). No separate `login` step is required—on the first run it may prompt for workspace trust and confirmation for mutating actions.

3. Run (choose a project root):
```bash
gemcode -C /path/to/project
```

Why `-C` matters:
- where `.gemcode/` state is stored
- which local instructions/skills are active
- what trust scope and permission boundary apply

## Quick examples

### Start the interactive REPL

```bash
gemcode -C /path/to/project
```

### Run a one-shot prompt

```bash
gemcode -C /path/to/project "Explain how authentication works in this repo"
```

### Allow a mutating run

```bash
gemcode -C /path/to/project --yes "Fix the failing tests and explain the change"
```

### Attach a file to a one-shot turn

```bash
gemcode -C . --attach ./report.pdf "Summarize this and list the key risks"
```

### Start the scheduler

```bash
gemcode kaira -C .
```

### Start the IDE bridge

```bash
gemcode ide --stdio
```

## Try these REPL commands

Once you start `gemcode -C /path/to/project`, use slash commands for the high-signal operations:

```text
/help
/status
/cost
/context
/attach ./file.pdf
/skills
/create gemskill
/audit
/diff
/rewind
```

## Execution modes

| Mode | Purpose |
|---|---|
| One-shot CLI | Single prompt/response tasks |
| REPL | Stateful terminal interaction |
| TUI | Scrollback terminal UI on top of the REPL runtime |
| IDE stdio | Editor integration over stdin/stdout |
| Kaira | Background job queue and scheduler |
| Live audio | Microphone-driven Gemini Live sessions |

## Key concepts

### Project root

Every GemCode run is anchored to a project root. This is one of the most important design decisions in the system because it controls visibility, state placement, trust boundaries, and which repo-local assets are loaded.

### `.gemcode/`

GemCode stores project-local runtime state under `.gemcode/`. Depending on what features you use, this can include:

- sessions
- logs
- tool results
- memory data
- skills
- rules
- output styles
- hooks
- MCP and OpenAPI config
- eval artifacts

### GemSkills

GemSkills are reusable playbooks stored as markdown assets. They let you codify repeatable workflows, domain-specific instructions, output formats, and evidence rules without hardcoding all of that logic into the base agent prompt.

### Permissions and trust

GemCode combines workspace trust, permission settings, allow/deny policies, and optional interactive confirmation for mutating operations. This helps keep repository access explicit instead of silently granting unrestricted tool execution.

## Feature highlights

### Repository-native tooling

GemCode is designed to work on actual codebases, not just pasted snippets. It can inspect files, search the repo, edit source, manage checkpoints, and coordinate shell-driven workflows in the context of a chosen project root.

### Persistent sessions

Sessions are stored locally and can be resumed. This makes GemCode useful for longer-running implementation work rather than only stateless prompt/response usage.

### Token and context management

The runtime includes budget-aware behaviors such as context reporting, token/cost visibility, and tool-result offloading for large outputs. This matters for bigger repositories and longer sessions.

### Extensible local assets

GemCode can load project-local instructions, rules, styles, hooks, skills, MCP configs, and OpenAPI definitions. This makes it adaptable to different teams and repositories without turning the whole system into a hardcoded monolith.

### Optional capability layers

Depending on configuration, GemCode can also expose deep research, embeddings, memory-backed retrieval, browser/computer-use flows, and live audio support.

## Power features (high impact)

GemCode is not just “chat on code”. These are the things power users typically reach for repeatedly:

- **Inspection & recovery**: checkpoints and rewinds (`/diff`, `/rewind`), plus an audit trail (`/audit` and `.gemcode/audit.log`)
- **Cost/context telemetry**: `/cost`, `/status`, `/context`, plus compression/limits controls like `/compact`, `/budget`, `/limits`
- **Multimodal attachments**: CLI `--attach` / `--image` for the current one-shot turn; REPL `/attach` with aliases like `/image` / `/file`
- **GemSkills as reusable workflows**: `/skills`, `/gemskill`, `/append`, and `/create gemskill` (wizard-driven)
- **Evaluation & autotune gates**: `gemcode eval -C .`, `gemcode autotune init --tag name -C .`, `gemcode autotune eval -C .` (and REPL `/eval` / `/autotune`)
- **Integrations & tool surfaces**: MCP via `.gemcode/mcp.json`, OpenAPI specs under `.gemcode/openapi/`, IDE stdio bridge, and web/SSE compatibility
- **Capability flags**: enable deep research with `--deep-research`, embeddings with `--embeddings`, and optional computer-use with `GEMCODE_ENABLE_COMPUTER_USE`

## Typical workflow

1. Install `gemcode`
2. Set `GOOGLE_API_KEY`
3. Start GemCode with `-C /path/to/project`
4. Trust the workspace if prompted
5. Ask questions, inspect architecture, or request changes
6. Use REPL commands or GemSkills when you need more structured workflows
7. Review diffs, costs, and context pressure as needed

## Common commands

```bash
gemcode models
gemcode -C .
gemcode -C . "Explain this repository"
gemcode -C . --attach ./diagram.png "Analyze this architecture diagram"
gemcode kaira -C .
gemcode live-audio -C .
gemcode ide --stdio
```

## Documentation map

The root README is the landing page. The deeper documentation lives here:

- User manual and navigation: [`gemcode/README.md`](gemcode/README.md)
- Docs index: [`docs/README.md`](docs/README.md)
- Install and first run: [`docs/install.md`](docs/install.md)
- CLI, REPL, TUI, and commands: [`docs/cli-and-repl.md`](docs/cli-and-repl.md)
- Configuration and local assets: [`docs/configuration.md`](docs/configuration.md)
- Tools and permissions: [`docs/tools-and-permissions.md`](docs/tools-and-permissions.md)
- Capability bundles: [`docs/capabilities.md`](docs/capabilities.md)
- Integrations: [`docs/integrations.md`](docs/integrations.md)
- Architecture deep dive: [`docs/architecture.md`](docs/architecture.md)
- Operations and troubleshooting: [`docs/operations.md`](docs/operations.md)
- `.gemcode/` state reference: [`docs/reference-gemcode-state.md`](docs/reference-gemcode-state.md)
- Web integration contract: [`docs/web-ui-contract.md`](docs/web-ui-contract.md)

## Repository structure

| Path | Purpose |
|---|---|
| `gemcode/` | Python package, CLI, runtime, tests, packaging |
| `docs/` | Production documentation set |

## Source install for contributors

If you want to develop GemCode itself rather than just use it:

```bash
cd gemcode
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
```

Optional extras:

```bash
python3 -m pip install -e ".[mcp]"
```

## Contributing

Contributions are welcome, especially for:

- core runtime improvements
- documentation
- GemSkills
- integrations
- tests and reliability

For source changes, run the relevant tests from `gemcode/`.

## Security & auditability

GemCode is built around explicit workspace trust and permission controls for filesystem/shell/git tool access.

Runtime activity is recorded for inspection:
- `.gemcode/audit.log`
- REPL command `/audit`

For the full model (tool allowlists, permissions, and failure modes), see [`docs/tools-and-permissions.md`](docs/tools-and-permissions.md).

## License

See [`gemcode/LICENSE`](gemcode/LICENSE).

