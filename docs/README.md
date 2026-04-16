# GemCode documentation index

## Primary manual (start here)

- **[`../gemcode/README.md`](../gemcode/README.md)** — Full manual: what GemCode is (sessions, memory types, GemSkill workflows), architecture, install, CLI and flags, `.gemcode/` layout, **`GEMINI.md`**, function tools catalog, REPL slash commands (including **`/eval`**, **`/gemskill`**, **`/append gemskill`**), curated memory, workspace trust, styles, rules, checkpoints, multi-root, model routing, capabilities, permissions, hooks, token policy, MCP, IDE stdio bridge, eval/autotune, Kaira, live audio, related repos, environment variables, development and PyPI release.

## Recent additions

- **Caveman output styles** — built-in terse modes via `/caveman` (plus `wenyan-*` variants).
- **Memory file compression** — `compress_memory_file` tool and `/caveman:compress` alias (creates a backup and validates headings/URLs/code blocks).
- **Search query sanitization** — `web_search` and `semantic_search_files` auto-trim “contaminated” long queries to the likely intended question.
- **GemSkills** — improved YAML frontmatter support (`description: >` / `|`), token-efficient skill invocation, and a REPL wizard (“I want to make a new skill”) to generate new skills quickly.
- **WAL** — `.gemcode/wal.jsonl` metadata log for curated memory appends and memory compression writes.

## Web and HTTP

- **[`web-ui-contract.md`](web-ui-contract.md)** — Expected health checks, `POST /api/chat` streaming (SSE-style `data:` frames), `StreamChunk` and `StreamEvent` shapes for compatibility with the reference web UI. Use this when implementing or adapting a GemCode-backed API server.

## Editor integration

- **[`../gemcode-vscode/README.md`](../gemcode-vscode/README.md)** — VS Code extension: secure API key storage, launch commands, Chat panel, diff apply workflow, and the `gemcode ide --stdio` JSONL bridge.

## Repository root

- **[`../README.md`](../README.md)** — Project overview, quickstart, and links to the documents above.
