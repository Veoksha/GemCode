# GemCode Documentation Index

This directory is the production documentation set for GemCode.

## Quickstart: multi-agent in 5 minutes

This is the fastest path to “agents managing agents” with background scheduling.

### Start the GemCode Runtime (background jobs + automations)

In a separate terminal:

```bash
gemcode runtime -C . --automations
# (alias)
gemcode kaira -C . --automations
```

### Create an agent (workspace + registry)

In your main terminal (REPL/TUI):

```text
/agent create verifier "Verifier" subagent manager verifier "Independent review and sanity checks."
```

This creates:
- an agent registry entry in `.gemcode/org.json`
- a workspace under `.gemcode/agents/<id>-<slug>/` (with `AGENT.md` and `workspace/` files)

### Run GemCode “as that agent”

In another terminal:

```bash
gemcode -C .gemcode/agents/<id>-<slug>
```

The agent’s local constitution under `workspace/` is automatically injected into its instruction.

### Trigger agent-to-agent work

Back in the main terminal:

```text
/agent list
/agent assign verifier Review the last change for risks + missing tests
```

(`/agents …` is the same as `/agent …`.)

Notes:
- `/agent assign` publishes to the runtime bus when available, so it runs autonomously in the background.
- Agents can also delegate to other agents using the `org_*` tools in normal mode (no slash commands required).
- Completed delegation and worker results are recorded in **`.gemcode/fleet_reports.jsonl`** and prepended to your **next** manager turn (and optional auto-continue is available). See [`orchestration.md`](orchestration.md#fleet-report-inbox--auto-continue-hands-off-summaries).
- **In-process mesh + habits** (no runtime daemon required): many schedules live in one **`.gemcode/habits.json`** (each row names an org member); each member still gets **their own** skills/workspace/session when a job runs. Removing a habit does **not** cancel jobs already queued—use **`/mesh halt`** or **`mesh_halt`**. See [`orchestration.md`](orchestration.md#stopping-background-work-habits-removed-but-jobs-still-finishing).

### Watch what’s running (optional)
In yet another terminal, you can attach to the runtime event stream to see job progress and bus messages:

```bash
gemcode runtime attach -C .
```

### Schedule a recurring job

From REPL/TUI (manual):

```text
/automations init nightly-health
/automations run nightly-health
```

Or from the model in normal mode (tools):
- `automations_init(name, ...)`
- `automations_run(name)`

Next: read [`orchestration.md`](orchestration.md) for the full runtime + fleet model.

## Start here
- [`../README.md`](../README.md) — repository overview, quickstart, and documentation map
- [`../gemcode/README.md`](../gemcode/README.md) — primary user manual and navigation page

## Core documentation
- [`architecture.md`](architecture.md) — subsystem map, runtime flows, runner assembly, tool-loading surfaces, and persistence architecture
- [`install.md`](install.md) — requirements, install, upgrade, first run, and common setup problems
- [`cli-and-repl.md`](cli-and-repl.md) — execution modes, flags, REPL/TUI behavior, attachments, and session flows
- [`configuration.md`](configuration.md) — env vars, agent-instruction toggles, `.gemcode/` assets, project instruction files, rules, styles, skills, hooks, MCP, and OpenAPI
- [`tools-and-permissions.md`](tools-and-permissions.md) — tool families, permission layers, super mode (full autonomy), background tasks, IDE proposal behavior, and AFC implications
- [`capabilities.md`](capabilities.md) — deep research, embeddings, memory, VeoMem, browser/computer use, (experimental) live audio, and routing behavior
- [`integrations.md`](integrations.md) — IDE stdio, web/SSE, MCP, OpenAPI, browser integration, and skills as a workflow surface
- [`operations.md`](operations.md) — audit/debugging, common failures, GemCode Runtime operations, eval/autotune, and release workflow
- [`hosted.md`](hosted.md) — multi-tenant `gemcode serve` on GCP (GKE pod-per-user)
- [`reference-gemcode-state.md`](reference-gemcode-state.md) — quick reference for the `.gemcode/` directory layout and state files

## Integration contracts
- [`web-ui-contract.md`](web-ui-contract.md) — HTTP/SSE contract for `gemcode serve` and compatible frontends (UI repos are optional clients)

## Notes
- The documentation is organized by operator concern rather than by file name.
- **Web UIs** are not part of the `gemcode` PyPI package; run `gemcode serve` and connect any frontend to `http://127.0.0.1:3001`.
- The code remains the final source of truth; documentation should track `gemcode/src/gemcode/` behavior closely.
