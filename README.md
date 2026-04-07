# GemCode

Local-first coding agent: **Gemini** + **[Google ADK](https://google.github.io/adk-docs/)**.

GemCode is a clean-room implementation (reference only to third-party Claude
Code trees) that combines:

- Project tools (read/edit files + optional shell)
- Optional modality toolsets (Deep Research, Embeddings, Computer Use, Live Audio)
- Permission gates, audit logging, circuit breaker, and recovery-loop behavior
- Session persistence for multi-turn work

GemCode has evolved quickly — the **authoritative, detailed manual** (CLI, env vars,
tools, policies, token optimizations, VS Code extension, and release workflow) is:

- **[`gemcode/README.md`](gemcode/README.md)**

## Quickstart (TL;DR)

Requirements:

- Python 3.11+
- `GOOGLE_API_KEY` (get one from Google AI Studio)

Install editable (recommended for development):

```bash
cd gemcode
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Run:

```bash
gemcode -C . "Explain the codebase structure"
gemcode -C . --yes "Fix the failing tests"
gemcode -C . --session myproj --yes "Continue: implement the refactor"
```

## What makes GemCode “smart” (high signal)

- **Dynamic token policy**: tool output caps adapt to **context pressure** and **task risk**.
- **Self-tuning per-repo profile**: GemCode learns a repo’s “difficulty” over time via
  `.gemcode/policy.json` and adjusts evidence budgets automatically.
- **Stable tool output offloading**: oversized outputs are stored under `.gemcode/tool-results/`
  and referenced as `tool_result:<sha>` so context stays clean and cache-friendly.
- **Repo map**: `repo_map()` gives a compact symbol-first view of large repos; read full files on demand.

## Features (all implemented powers)

1. **Model routing**
   - `--model-mode` / `GEMCODE_MODEL_MODE`: `fast|balanced|quality|auto`
   - `GEMCODE_MODEL_FAMILY_MODE`: `primary|alt|auto`
   - Capability-aware model selection for deep-research/computer/audio
   - Gemini 3 tool combination control: `--tool-combination-mode` / `GEMCODE_TOOL_COMBINATION_MODE`

2. **Capability routing (auto or forced)**
   - `--capability-mode` / `GEMCODE_CAPABILITY_MODE`: `auto|research|embeddings|computer|audio|all`
   - This flips tool injection on/off before the model runs.

3. **Custom tools for your repo**
   - Read-only: `read_file`, `list_directory`, `glob_files`, `grep_content`
   - Mutating (requires `--yes` unless denied by policy): `write_file`, `search_replace`
   - Optional shell execution (allowlist gated): `run_command`

4. **Deep research (built-in Gemini tools)**
   - Injects Gemini built-in tools:
     - `google_search`, `url_context`
     - `google_maps_grounding` (optional; injected only when you opt-in via
       `--maps-grounding` / `GEMCODE_ENABLE_MAPS_GROUNDING=1`)
   - On Gemini 3.x, GemCode can enable built-in tool context circulation so
     built-in results can be combined with your custom tools.

5. **Embeddings (semantic retrieval + memory)**
   - Semantic tool: `semantic_search_files`
   - Embedding-backed persistent memory: `EmbeddingFileMemoryService`

6. **Computer Use (optional, browser automation)**
   - Playwright-backed `BrowserComputer` + ADK `ComputerUseToolset`
   - Permission-gated: requires `--yes` in default mode; denied in strict mode.

7. **Live Audio (optional, streaming)**
   - `gemcode live-audio` uses ADK Live API + `LiveRequestQueue`
   - Requires `sounddevice` + `numpy`

8. **Safety + reliability**
   - Permission gates: `GEMCODE_PERMISSION_MODE=strict|default`
   - Circuit breaker: `GEMCODE_MAX_CONSECUTIVE_TOOL_FAILURES`
   - Recovery-loop: tool failure retry (skip policy denials and circuit breaker blocks)

9. **Observability + control flow**
   - Audit log: `.gemcode/audit.log` (tool usage + terminal reasons)
   - Optional per-tool summaries: `GEMCODE_EMIT_TOOL_USE_SUMMARIES=1`
   - Token budget & stops: `GEMCODE_TOKEN_BUDGET`, `GEMCODE_MAX_SESSION_TOKENS`
   - Stop-the-loop hook: `GEMCODE_POST_TURN_HOOK` or `.gemcode/hooks/post_turn`

10. **Prompt suggestions**
   - Heuristic next-step messaging (`gemcode/prompt_suggestions.py`)
   - Optional Interactions API-based improvement:
     `GEMCODE_PROMPT_SUGGESTIONS_USE_INTERACTIONS=1`

11. **Optional MCP**
   - `--mcp` loads `.gemcode/mcp.json` toolsets
   - Install extra: `pip install -e ".[mcp]"`

## Tool audit

- `gemcode tools list` and `gemcode tools smoke` enumerate and validate the
  tool set active for a given config (deep research/embeddings/maps grounding).

## Docs

Full, detailed documentation (including CLI flags, env vars, and tool
behavior) lives in [`gemcode/README.md`](gemcode/README.md).

## Web UI (Claude Code compatible)

This workspace includes a Claude Code–style web UI in [`claude-code-leaked/web`](claude-code-leaked/web) and a documented backend contract in [`docs/claude-web-contract.md`](docs/claude-web-contract.md).

Run instructions and environment variables are in [`web-ui/README.md`](web-ui/README.md).

