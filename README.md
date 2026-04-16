# GemCode

Local-first coding agent for real repositories: read, edit, search, and run allowlisted commands with explicit permissions and inspectable history.

## What it is
GemCode is a single Python package that exposes a `gemcode` CLI. Every run is anchored to a **project root** (`-C`) and uses Gemini + ADK to orchestrate tool calls while keeping a trace of what happened under `.gemcode/`.

## Key features
- **Repository-native workflow**: file reads/edits, search, and structured tool usage against your codebase
- **Explicit safety model**: workspace trust + permission gating + optional interactive approvals
- **Persistent sessions**: SQLite-backed conversation history per session id
- **GemSkills**: reusable markdown playbooks (`.gemcode/skills/<name>/SKILL.md`) for repeatable workflows
- **Token/context controls**: budgets, compaction, and tool-result offloading
- **Multiple execution modes**:
  - One-shot CLI
  - REPL / scrollback TUI
  - IDE stdio bridge (`gemcode ide --stdio`)
  - Background queue via **Kaira**
  - Live audio mode
- **Integrations**: MCP and OpenAPI tool loading, plus optional browser/computer-use and deep-research capabilities

## Quickstart

### 1) Install (editable)
```bash
cd gemcode
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
```

### 2) Set your Gemini API key
```bash
export GOOGLE_API_KEY="your-key"
```

### 3) Run against a project
```bash
gemcode -C /path/to/project
```

### One-shot example
```bash
gemcode -C /path/to/project "Explain how this repo is structured"
```

### Attach files (one-shot CLI)
```bash
gemcode -C . --attach ./report.pdf "Summarize this report"
```

## Documentation
- User manual + navigation: [`gemcode/README.md`](gemcode/README.md)
- Production docs index: [`docs/README.md`](docs/README.md)
- Architecture deep dive: [`docs/architecture.md`](docs/architecture.md)
- Configuration + local assets: [`docs/configuration.md`](docs/configuration.md)
- Tools + permissions: [`docs/tools-and-permissions.md`](docs/tools-and-permissions.md)
- Capabilities: [`docs/capabilities.md`](docs/capabilities.md)
- Integrations: [`docs/integrations.md`](docs/integrations.md)
- Operations and troubleshooting: [`docs/operations.md`](docs/operations.md)

## Project structure (high level)
- `gemcode/` — Python package + CLI entrypoint
- `docs/` — production documentation set
- `veomem/` — optional memory subsystem used by GemCode integrations
- `gemcode-vscode/` — VS Code extension (in full repository snapshots)
- `gemcode-web-api/` + `gemcode-web-ui/` — reference web wiring (in full repository snapshots)

## Security
GemCode includes:
- **workspace trust** (controls access to filesystem/shell/git tools)
- **permission modes and allow/deny patterns**
- **optional interactive approval** for mutating operations

If you discover a security issue, please open a GitHub issue with enough detail to reproduce it.

## Contributing
Contributions are welcome:
- bug fixes
- documentation improvements
- new GemSkills

Before making large changes, check `gemcode/` tests and run `pytest` from the project root (or inside the package).

## License
See [`gemcode/LICENSE`](gemcode/LICENSE).

