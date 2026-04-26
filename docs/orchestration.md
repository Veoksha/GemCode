# Orchestration: Kaira daemon (GemCode Runtime), agent fleet, and parallel work

This page documents the “multi-agent” surfaces in GemCode: the **Kaira daemon** (GemCode Runtime), the **agent fleet** registry (members + workspaces), and the automatic manager behaviors that can route work and improve worker skills over time.

## What changed (high-level improvements)

GemCode gained these orchestration features:

- **Kaira IPC (two-way)**: a running `gemcode runtime` daemon (alias: `gemcode kaira`) exposes a Unix-socket JSONL control plane and event stream.
- **Persistent job registry**: runtime jobs are stored under `.gemcode/kaira/jobs/` (queued/running/finished/failed, timestamps, last output).
- **Live event streaming**: the TUI can subscribe and display job lifecycle + text/tool deltas while you keep using GemCode normally.
- **HITL bridge**: background jobs can request approvals (tool confirmations) and the interactive TUI can answer them.
- **Agent fleet registry**: you can model a “fleet/org chart” of members (roles), each with a workspace under `.gemcode/agents/`.
- **Parallel subtasks**: the agent can run multiple isolated subtasks concurrently (`spawn_subtasks`).
- **Manager automation**: optional heuristics can auto-fan-out complex prompts, auto-route pre-review to org members, and auto-improve member skills when formatting/contracts aren’t met.

## Kaira daemon (GemCode Runtime)

### Start the daemon

In a separate terminal:

```bash
gemcode runtime -C .
# (alias)
gemcode kaira -C .
```

The runtime reads prompts from stdin (and can also be controlled over IPC). Each prompt becomes a queued job.

### Local scheduled automations (hourly/nightly/cron)

Kaira can run local scheduled automations from:
- `.gemcode/automations/*.json`

Enable:

```bash
gemcode runtime -C . --automations
```

Optional heartbeat (enqueue a status prompt every N seconds):

```bash
gemcode runtime -C . --heartbeat-every-s 240 --heartbeat-prompt "Heartbeat: summarise XAUUSD status"
```

#### Managing automations from the model (normal GemCode mode)
In addition to the `/automations ...` slash commands, the main GemCode agent can manage schedules via function tools:

- `automations_list()` — list automation configs under `.gemcode/automations/*.json`
- `automations_init(name, prompt=..., trigger_kind=nightly|daily|interval|cron, ...)` — create an automation config
- `automations_run(name)` — enqueue an automation immediately via runtime IPC (requires a running runtime socket)

### Single-terminal mode (TUI + embedded Kaira)

If you want Kaira to run without a second terminal, embed it in the scrollback TUI:

```bash
GEMCODE_TUI_WITH_KAIRA=1 gemcode -C .
```

The TUI will start Kaira headless (IPC-only) and also auto-subscribe to its event stream, so job output appears inline.

### TUI auto-connect + stream events

Start your normal GemCode TUI/REPL:

```bash
gemcode -C .
```

If a Kaira socket exists, GemCode will auto-connect and print:
- job queued/started/finished/failed/cancelled
- text deltas (streamed output)
- tool call + tool result summaries
- permission requests (HITL)

### Control-plane commands (from GemCode REPL/TUI)

These talk to the Kaira daemon via IPC:

```text
/kaira jobs
/kaira job <job_id_prefix>
/kaira cancel <job_id_prefix>
```

### Follow a single job (reduce noise)

In the TUI:

```text
/kaira follow <job_id_prefix>
/kaira unfollow
```

This filters the Kaira event stream to a single job id prefix.

## Agent fleet (“GemCode as an organisation”)

Agent registry data is stored under `.gemcode/org.json`.

Each agent also has a dedicated workspace under:
- `.gemcode/agents/<id>-<slug>/`

Inside an agent workspace, GemCode supports an optional “constitution” folder:
- `workspace/GOALS.md`
- `workspace/POLICIES.md`
- `workspace/SKILLS.md`
- `workspace/HEARTBEAT.md`
- `workspace/skills/*/SKILL.md`

When you run `gemcode -C .gemcode/agents/<id>-<slug>`, those files are automatically injected into the agent instruction.

### List and view the agent tree

```text
/agent list
/agent tree
```

### Create agents (roles)

```text
/agent create <name> <title> [kaira_worker|subagent] [reports_to] [address] [description...]
```

Examples:

```text
/agent create verifier "QA / test planner" subagent manager verifier "Find risks, propose tests, review plans."
/agent create kaira "Background worker" kaira_worker manager kaira "Run parallel jobs and return structured reports."
```

### Delegate / trigger work to an agent

```text
/agent assign <member> <task...>
```

Notes:
- `/agent assign` publishes `topic=org.assign` to the runtime (if available) so work can run autonomously in the background.
- The runtime manager translates `org.assign` into a queued job that runs `org_delegate(...)` and emits `topic=org.report` bus messages up the reporting chain.

### Improve an agent (append to their skill)

```text
/agent improve <member> <lessons...>
```

### Repair/scaffold an agent workspace constitution

For older agents (or if you deleted the folder), you can re-scaffold the `workspace/` files:

```text
/agent workspace init <name|id>
```

## Automatic manager behaviors (optional)

These are opt-in via environment variables. They are designed to make GemCode behave like a manager: it can fan out work, route to the right “employees”, and enforce structured reports.

### Auto fan-out guidance (prompt decomposition)

When a prompt looks broad, GemCode can guide the model to run parallel subtasks first:

- `GEMCODE_AUTO_FANOUT=1`
- `GEMCODE_AUTO_FANOUT_THRESHOLD=0.6`

The model will be nudged to call:
- `spawn_subtasks(tasks=[...], max_concurrency=4)`

### Manager dispatch (deterministic pre-delegation)

When a prompt looks complex, GemCode can pre-delegate a quick “risk + verification steps” review to an agent (for example `verifier`) and inject that into the main agent prompt:

- `GEMCODE_MANAGER_DISPATCH=1`
- `GEMCODE_MANAGER_DISPATCH_THRESHOLD=0.7`

### Structured worker reports (JSON) + auto retry

When the TUI receives a Kaira `job_report` event, it prefers `report_json`. If missing, it can request a single “reformat as STRICT JSON” follow-up job:

- `GEMCODE_KAIRA_REPORT_RETRY=1`

### Self-improving members (auto skill tweaks)

If workers repeatedly fail the report contract (for example missing JSON reports), the manager can append a small lesson to that member’s skill automatically:

- `GEMCODE_ORG_AUTO_IMPROVE=1`

## Super mode with Kaira and sub-agents

Background jobs use the same `GemCodeConfig` as the daemon. For fully autonomous workers (no IPC tool-confirmation waits, no interactive `get_user_choice`), start the runtime with:

```bash
gemcode runtime -C . --super
```

Or set `GEMCODE_SUPER_MODE=1` in the environment before launching `gemcode runtime`.

Sub-agents spawned via `run_subtask` / `spawn_subtasks` inherit the parent config, so super mode applies to their tool list (including the non-interactive `get_user_choice`) when the parent session was started in super mode.

Details: [`tools-and-permissions.md`](tools-and-permissions.md#super-mode-fully-autonomous).

## Where the state lives

Common `.gemcode/` locations:

- **Kaira jobs**: `.gemcode/kaira/jobs/`
- **Kaira IPC socket**: `.gemcode/ipc.sock`
- **Agent registry**: `.gemcode/org.json`
- **Role skills**: `.gemcode/skills/<member-name>/SKILL.md`
- **Agent workspaces**: `.gemcode/agents/<id>-<slug>/`

