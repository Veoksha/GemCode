# Reference: `.gemcode/` State

## Purpose
This page is a quick reference for the project-local state GemCode stores under `.gemcode/`.

## Core files and directories

| Path | Purpose |
|---|---|
| `.gemcode/sessions.sqlite` | ADK session history store |
| `.gemcode/sessions_meta.json` | Session names and metadata |
| `.gemcode/audit.log` | Audit log for tool and runtime activity |
| `.gemcode/fleet_reports.jsonl` | Inbox for completed `org.report` / `job.report` / `agent.report` (drained into the next manager turn when `GEMCODE_FLEET_REPORTS_INJECT=1`) |
| `.gemcode/tool-results/` | Offloaded large tool outputs |
| `.gemcode/artifacts/` | Artifact storage |
| `.gemcode/policy.json` | Dynamic token/evidence policy profile |
| `.gemcode/notes.md` | Operator/agent notes surfaced by `/notes` |
| `.gemcode/debug.yaml` | Optional debug log |
| `.gemcode/ipc.sock` | Manager IPC socket when `gemcode runtime` runs at this fleet root (default bind path unless overridden with `--socket`) |
| `.gemcode/manager_ipc.txt` | One-line absolute path to the manager IPC socket (written at fleet-root runtime start; supports `gemcode runtime --socket`) |
| `.gemcode/org.json` | Agent fleet registry (members, hierarchy, addresses, workspaces) |
| `.gemcode/agents/` | Per-agent workspaces (`<id>-<slug>/` with `AGENT.md`, optional `workspace/`, agent-local `.gemcode/`) |
| `.gemcode/kaira/` | Runtime daemon state directory (job registry, etc.; path name is historical) |

## Memory

| Path | Purpose |
|---|---|
| `.gemcode/GEMCODE_MEMORY.md` | Curated project memory |
| `.gemcode/GEMCODE_USER.md` | Curated user preferences |
| `.gemcode/memories.jsonl` | Retrieval memory backing store |
| `.gemcode/wal.jsonl` | Metadata log for curated memory and compression writes |

## Prompt assets

| Path | Purpose |
|---|---|
| `.gemcode/skills/` | GemSkills (and org member skills under `.gemcode/skills/<member>/SKILL.md`) |
| `.gemcode/output-styles/` | Output styles |
| `.gemcode/rules/` | Rule files |
| `.gemcode/hooks/` | Hook scripts |

## Runtime jobs (daemon)

| Path | Purpose |
|---|---|
| `.gemcode/kaira/jobs/` | Persisted job records for **`gemcode runtime`** (queued/running/finished/failed + timestamps + last output) |

## Integrations and policy

| Path | Purpose |
|---|---|
| `.gemcode/mcp.json` | MCP server configuration |
| `.gemcode/openapi/` | OpenAPI specs and related config |
| `.gemcode/settings.json` | Permission configuration |

## Evaluation

| Path | Purpose |
|---|---|
| `.gemcode/evals/last_eval.json` | Last evaluation run |
| `.gemcode/evals/autotune_ledger.jsonl` | Autotune history |

## Notes
- Not every file exists in every project.
- Some files are created lazily.
- Some state also exists under `~/.gemcode/` for user-global configuration and credentials.
