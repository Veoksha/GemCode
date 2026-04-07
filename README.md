# GemCode

**Local-first coding agent** built on **Google Gemini** and the **[Agent Development Kit (ADK)](https://google.github.io/adk-docs/)**. GemCode runs an agent loop over your repository: read and edit files, run allowlisted shell commands, search the web, optionally use deep research, embeddings, browser automation, and live audio — with explicit permissions, session persistence, checkpoints, and audit trails.

GemCode is implemented as an independent **clean-room** design on top of Gemini and ADK.

---

## What GemCode is

| Layer | Role |
|--------|------|
| **Model** | Gemini (configurable family, mode, and per-capability routing). |
| **Orchestration** | ADK `Runner` / `LlmAgent`: model ↔ tools until the turn completes or limits hit. |
| **Tools** | Function tools for filesystem, grep, repo map, edit, shell, web, notebooks, skills, memory, checkpoints, subtasks, etc. |
| **Session** | SQLite-backed history under `.gemcode/sessions.sqlite`; reusable `--session` ids. |
| **Safety** | Permission modes, optional in-run approval (HITL), allowlists, circuit breaker, recovery retries. |
| **UX** | CLI one-shot prompts, interactive REPL with slash commands, optional scrollback TUI, VS Code extension, `gemcode ide --stdio` for editor bridges, optional web backends. |

---

## Documentation map

| Document | Contents |
|----------|----------|
| **[`gemcode/README.md`](gemcode/README.md)** | **Primary manual**: install, CLI, flags, `.gemcode/` layout, tools, slash commands, skills, styles, rules, checkpoints, evals, hooks, IDE mode, Kaira, live audio, env vars. |
| **[`docs/README.md`](docs/README.md)** | Index of repo docs (web UI contract, etc.). |
| **[`docs/web-ui-contract.md`](docs/web-ui-contract.md)** | HTTP/SSE shapes for web frontends compatible with the reference UI. |
| **[`gemcode-vscode/README.md`](gemcode-vscode/README.md)** | VS Code extension: commands, settings, Chat + diff apply + `gemcode ide --stdio`. |

---

## Quickstart

**Requirements:** Python 3.11+, `GOOGLE_API_KEY` ([Google AI Studio](https://aistudio.google.com/app/apikey)).

```bash
cd gemcode
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

Copy `gemcode/.env.example` to `.env` and set `GOOGLE_API_KEY`, or run once:

```bash
gemcode login
```

**One-shot from a project root:**

```bash
gemcode -C /path/to/repo "Explain how authentication works"
gemcode -C /path/to/repo --yes "Fix the failing test in tests/test_foo.py"
```

**Interactive REPL** (no prompt argument):

```bash
gemcode -C /path/to/repo
```

Type natural language, or slash commands (`/help`, `/status`, `/diff`, …). Exit with `/exit` or Ctrl+D.

---

## Feature highlights (short)

- **Dynamic token policy** — Tool output caps and risk-aware budgets; self-tuning `.gemcode/policy.json`.
- **Tool output offloading** — Large outputs stored under `.gemcode/tool-results/` as stable `tool_result:<sha>` references.
- **Repo map** — Compact symbol-oriented overview; read full files on demand.
- **GemSkills** — `.gemcode/skills/<name>/SKILL.md` (and `~/.gemcode/skills/`); `/skills`, `/skill`, tools `list_skills` / `load_skill`; built-in `/batch` orchestrator.
- **Output styles & rules** — `.gemcode/output-styles/*.md`, `.gemcode/rules/*.md` (optional path gating); `/style`, `/rules`.
- **Checkpoints** — Mutations can be tracked; `/diff`, `/rewind` (or `/checkpoint`).
- **Multi-root** — `/add-dir` for extra read/search roots with path safety.
- **Eval & autotune** — `gemcode eval`, `gemcode autotune init|eval` with ledger under `.gemcode/evals/`.
- **Optional powers** — Deep research (`google_search`, `url_context`), embeddings + memory, Playwright computer use, MCP (`.gemcode/mcp.json`), Kaira job daemon, `gemcode live-audio`.

---

## Repository layout

| Path | Purpose |
|------|---------|
| `gemcode/` | Python package (`pip install -e .`), CLI entry `gemcode`. |
| `gemcode-vscode/` | VS Code extension (launch CLI, Chat, stdio bridge). |
| `gemcode-web-api/` | Example Node HTTP/WebSocket server wiring terminals and chat. |
| `docs/` | Contracts and doc indexes. |

---

## PyPI releases

Tags matching `v*` can drive publishing (see `.github/workflows/` and **`gemcode/README.md` → Release workflow**).

---

## License

See the `LICENSE` file in this repository (and `gemcode/LICENSE` if present).
