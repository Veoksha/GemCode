# GemCode documentation index

## Primary manual (start here)

- **[`../gemcode/README.md`](../gemcode/README.md)** — Full manual: what GemCode is (sessions, memory types, GemSkill workflows), architecture, install, CLI and flags, `.gemcode/` layout, **`GEMINI.md`**, function tools catalog, REPL slash commands (including **`/eval`**, **`/gemskill`**, **`/append gemskill`**), curated memory, workspace trust, styles, rules, checkpoints, multi-root, model routing, capabilities, permissions, hooks, token policy, MCP, IDE stdio bridge, eval/autotune, Kaira, live audio, related repos, environment variables, development and PyPI release.

## Web and HTTP

- **[`web-ui-contract.md`](web-ui-contract.md)** — Expected health checks, `POST /api/chat` streaming (SSE-style `data:` frames), `StreamChunk` and `StreamEvent` shapes for compatibility with the reference web UI. Use this when implementing or adapting a GemCode-backed API server.

## Editor integration

- **[`../gemcode-vscode/README.md`](../gemcode-vscode/README.md)** — VS Code extension: secure API key storage, launch commands, Chat panel, diff apply workflow, and the `gemcode ide --stdio` JSONL bridge.

## Repository root

- **[`../README.md`](../README.md)** — Project overview, quickstart, and links to the documents above.
