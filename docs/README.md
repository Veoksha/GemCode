# GemCode Documentation Index

This directory is the production documentation set for GemCode.

## Start here
- [`../README.md`](../README.md) — repository overview, quickstart, and documentation map
- [`../gemcode/README.md`](../gemcode/README.md) — primary user manual and navigation page

## Core documentation
- [`architecture.md`](architecture.md) — subsystem map, runtime flows, runner assembly, tool-loading surfaces, and persistence architecture
- [`install.md`](install.md) — requirements, install, upgrade, first run, and common setup problems
- [`cli-and-repl.md`](cli-and-repl.md) — execution modes, flags, REPL/TUI behavior, attachments, and session flows
- [`configuration.md`](configuration.md) — env vars, `.gemcode/` assets, instruction files, rules, styles, skills, hooks, MCP, and OpenAPI
- [`tools-and-permissions.md`](tools-and-permissions.md) — tool families, permission layers, background tasks, IDE proposal behavior, and AFC implications
- [`capabilities.md`](capabilities.md) — deep research, embeddings, memory, VeoMem, browser/computer use, live audio, and routing behavior
- [`integrations.md`](integrations.md) — IDE stdio, web/SSE, MCP, OpenAPI, browser integration, and skills as a workflow surface
- [`operations.md`](operations.md) — audit/debugging, common failures, Kaira operation, eval/autotune, and release workflow
- [`reference-gemcode-state.md`](reference-gemcode-state.md) — quick reference for the `.gemcode/` directory layout and state files

## Integration contracts
- [`web-ui-contract.md`](web-ui-contract.md) — HTTP/SSE contract for compatible web frontends and backends

## Notes
- The documentation is organized by operator concern rather than by file name.
- The code remains the final source of truth; documentation should track `gemcode/src/gemcode/` behavior closely.
