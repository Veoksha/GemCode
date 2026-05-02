# Research notes

Competitor/market notes that used to live here were removed. Use this file as a **pointer hub** for shipped GemCode behavior.

## Primary docs

| Topic | Location |
| --- | --- |
| Repo overview | [`README.md`](README.md) |
| User manual | [`gemcode/README.md`](gemcode/README.md) |
| Doc index + multi-agent quickstart | [`docs/README.md`](docs/README.md) |
| Architecture (modes, subsystems, persistence) | [`docs/architecture.md`](docs/architecture.md) |
| Kaira runtime, org fleet, fleet inbox, auto-continue | [`docs/orchestration.md`](docs/orchestration.md) |
| Env vars and `.gemcode/` layout | [`docs/configuration.md`](docs/configuration.md) · [`docs/reference-gemcode-state.md`](docs/reference-gemcode-state.md) |
| CLI / REPL / terminal UI | [`docs/cli-and-repl.md`](docs/cli-and-repl.md) |

## Terminal UI (one implementation)

GemCode ships **one** interactive terminal UI: scrollback-style session in [`gemcode/src/gemcode/tui/scrollback.py`](gemcode/src/gemcode/tui/scrollback.py). Plain line REPL is used when `GEMCODE_TUI=0` or the TUI cannot start. There is no separate fullscreen TUI in this repository.

## Fleet report inbox (manager context)

Background `org.report` / `job.report` / `agent.report` completions append to **`.gemcode/fleet_reports.jsonl`** at the fleet root and are prepended to the next manager turn (and optional auto-continue). Details: [`docs/orchestration.md`](docs/orchestration.md#fleet-report-inbox--auto-continue-hands-off-summaries).

## External references

- [Google ADK documentation](https://google.github.io/adk-docs/)
