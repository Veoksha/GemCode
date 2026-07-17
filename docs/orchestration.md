# Orchestration: Agent Mesh, Event Bus, and Multi-Agent Coordination

This page documents GemCode's multi-agent orchestration system: the **Agent Mesh** (in-process coordination), the **Event Bus** (agent communication), **Self-Triggering Agents**, **Delegation Learning**, and the optional **A2A Bridge** for cross-machine agents.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         GemCode Process                              │
│                                                                     │
│  ┌──────────┐    ┌──────────────┐    ┌───────────────────────────┐ │
│  │ CLI/TUI  │───▶│  run_turn()  │───▶│       Agent Mesh          │ │
│  └──────────┘    └──────────────┘    │  ┌─────────────────────┐  │ │
│                                      │  │  Priority Queue     │  │ │
│                                      │  │  Concurrent Runner  │  │ │
│                                      │  │  Full-Power Agents  │  │ │
│                                      │  └──────────┬──────────┘  │ │
│                                      └─────────────┼─────────────┘ │
│                                                    │                │
│  ┌─────────────────────────────────────────────────▼─────────────┐ │
│  │                       Event Bus                                │ │
│  │  org.report │ job.report │ checkpoint.created │ agent.report   │ │
│  └──┬──────────────┬──────────────┬──────────────┬───────────────┘ │
│     │              │              │              │                   │
│  ┌──▼───┐    ┌────▼────┐   ┌────▼────┐   ┌────▼──────────────┐   │
│  │Fleet │    │Trigger  │   │Learning │   │Intelligence       │   │
│  │Report│    │Engine   │   │Loop     │   │Layer              │   │
│  └──────┘    └─────────┘   └─────────┘   └───────────────────┘   │
│                                                                     │
│  [Optional: GemCode Runtime daemon — queue + IPC + file automations]  │
│  [Optional: A2A Bridge for cross-machine agents]                    │
└─────────────────────────────────────────────────────────────────────┘
```

## Agent Mesh (In-Process Orchestration)

The Agent Mesh is the primary orchestration layer. It runs in a **dedicated daemon thread** inside the GemCode process with its **own asyncio event loop** (see `gemcode/src/gemcode/agent_mesh.py`). That separation matters for the GemCode TUI: the TUI’s loop is often blocked on `prompt_toolkit` input, so mesh jobs, **habits**, and **triggers** would not make reliable progress on the same loop. With the background thread, work keeps running; outcomes still land in **`.gemcode/fleet_reports.jsonl`** and are drained on the **next** user turn (or via bus subscribers).

No separate **`gemcode runtime`** process is required for **`org_delegate`** or mesh-backed background agents.

### What it does
- Manages a priority queue of agent jobs
- Runs jobs concurrently (default: 3 parallel agents)
- **Per-session turn serialization**: two jobs targeting the **same** agent (same workspace + stable session id) still run **`run_turn` one at a time** for that session. This matches ADK’s SQLite session semantics and avoids “stale session” / `last_update_time` errors when overlapping habits or delegations would otherwise append events concurrently.
- Each job gets a **full-power Runner** (same as the TUI/CLI — all tools, model routing, memory, MCP)
- Results are published to the Event Bus and persisted to fleet reports
- Auto-starts when the first job is enqueued (background thread + scheduler)

### Per-agent context (fleet schedule vs member runtime)

- **Habits** are defined in **one** fleet file, `.gemcode/habits.json`, with **many** entries. Each entry names an **`agent`** (org member) plus prompt and schedule—so *different agents can have different habits at the same time*.
- **Skills** and **session state** are *not* inside that JSON: each member uses their org **`skill_name`**, optional **agent workspace** under `.gemcode/agents/<id>-<slug>/` (local `.gemcode/`, skills, memory), and their own **ADK SQLite session** when the mesh runs a job for them.
- The manager’s **`ensure_mesh` / `get_mesh`** calls keep the mesh singleton aligned with the active **`project_root`** (e.g. after `gemcode -C`) so habits, triggers, and fleet paths resolve to the same tree as the UI.

### Key difference from older releases
- **`org_delegate`** no longer attempts Kaira/runtime IPC. The mesh is the primary path; an in-process **subtask** fallback runs if the mesh path fails.
- Optional **`gemcode runtime`** remains useful for a **separate** fleet-manager queue, IPC attach, **`/agent assign`** / **`/agent trigger`** when the socket is up, and **`.gemcode/automations/`** — not for basic `org_delegate`.

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMCODE_MESH_CONCURRENCY` | 3 | Max concurrent mesh jobs |

## Event Bus (Agent Communication)

The Event Bus is an in-memory pub/sub system that enables agent-to-agent communication without Unix sockets.

### Topics

| Topic | Published when | Subscribers |
|-------|---------------|-------------|
| `job.queued` | Job enqueued on mesh | TUI, triggers |
| `job.started` | Job begins execution | TUI, triggers |
| `job.report` | Job completes or fails | Triggers, learning, fleet reports |
| `org.report` | Org delegation completes | Triggers, learning, fleet reports |
| `agent.report` | Subtask / mesh worker status | Fleet reports |
| `agent.dm` | Mesh worker sends a direct message to another member | Fleet reports (formatted inbox line) |
| `agent.broadcast` | Mesh worker broadcasts to fleet + manager | Fleet reports (formatted inbox line) |
| `checkpoint.created` | Files are about to be modified | Triggers (opt-in verification) |
| `org.assign` | Delegation request (e.g. in-process bus) | Mesh handler (`delegate_to_member`) |

### How agents communicate
1. Agent A completes work → publishes `org.report` to bus
2. Trigger Engine sees the event → auto-activates Agent B (e.g., verifier)
3. Agent B runs verification → publishes its own `job.report`
4. Learning Loop records both outcomes
5. Next turn: fleet reports are drained into the user's prompt

### Agent-to-agent messaging (mesh workers)

While running inside a mesh job, agents get extra tools (see `AgentMesh._build_mesh_tools_for_job`):

- **`agent_dm(to, message)`** — publishes `agent.dm` on the bus and appends a line to **`fleet_reports.jsonl`** (manager sees it on the next drain).
- **`agent_broadcast(message)`** — publishes `agent.broadcast` and appends to **`fleet_reports.jsonl`**.

Formatted inbox examples (from `fleet_reports.py`): `[agent.dm] verifier → kaira: "…"`, `[agent.broadcast] kaira: "…"`.

## Self-Triggering Agents

Agents can auto-activate when specific bus events occur. Configured via `.gemcode/triggers.json`.

### Default triggers

| Agent | Watches | Condition | Action |
|-------|---------|-----------|--------|
| verifier | `job.report` | status=finished | Review completed work for correctness |
| kaira | `job.report` | status=failed | Diagnose failure and attempt fix |
| verifier | `checkpoint.created` | (any) | Verify changed files (disabled by default) |

### Managing triggers

From the agent (tools):
```
triggers_list()           — show all triggers
triggers_add(agent, on_topic, action, when={...}, cooldown_s=60)
triggers_remove(agent)    — remove all triggers for an agent
```

From the filesystem:
```json
// .gemcode/triggers.json
{
  "triggers": [
    {
      "agent": "verifier",
      "on_topic": "job.report",
      "when": {"status": "finished"},
      "action": "Review the completed job output.",
      "cooldown_s": 120,
      "enabled": true
    }
  ]
}
```

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMCODE_AGENT_TRIGGERS` | 1 | Enable self-triggering agents |

## Delegation Learning

GemCode remembers which agents succeed at which tasks and uses that history for future routing.

### How it works
1. Every delegation outcome (member, task, status, duration) is recorded to `.gemcode/delegation_memory.jsonl`
2. `suggest_agent_for_task(task)` analyzes history and recommends the best agent
3. The intelligence layer injects delegation hints into prompts
4. Over time, routing gets smarter without manual configuration

### Tools

```
suggest_delegate(task)    — get a recommendation based on history
org_delegate(member, task, context)  — delegate with auto-learning
```

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMCODE_DELEGATION_LEARNING` | 1 | Enable delegation memory |

## Intelligence Layer

The intelligence layer makes **structural decisions** (not prompt injection) that connect all systems:

### Pre-turn (before the model runs)
- Checks delegation history → suggests best agent for the task
- Auto-enables capabilities based on project profile (e.g., if this project always uses web search, enable it)
- Injects minimal delegation context (facts, not instructions)

### Post-turn (after the model completes)
- Records which tools were used → updates project profile
- If risky changes detected (3+ file writes) → auto-triggers verifier via mesh
- Delegation outcomes → stored for future routing

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMCODE_AGENT_INTELLIGENCE` | 1 | Enable intelligence layer |
| `GEMCODE_AUTO_VERIFY` | 1 | Auto-verify after risky changes |

## Codebase Awareness

GemCode builds a persistent understanding of the project that compounds over time. Unlike other agents that re-discover the codebase every turn, GemCode starts each turn already knowing the project structure, recent changes, and learned facts.

### Three layers

**Structure Graph** — What files exist, what they export, what imports what. Built incrementally from every `read_file` call using lightweight regex extraction. Supports Python and TypeScript/JavaScript.

**Change Journal** — What changed, when, and what the outcome was. Built from every `write_file`, `search_replace`, and `bash` call.

**Insight Cache** — Learned facts about this specific codebase. Built from tool outcomes and test results.

### Impact analysis

When a file changes, `get_affected_files()` returns files likely affected — via import tracking and learned correlations. The self-healing loop uses this to run only relevant tests.

### Enriched grep

Grep results are enriched with structure info (exports, line count, symbol count) so the agent can infer what a file does without reading it.

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMCODE_CODEBASE_AWARENESS` | 1 | Enable codebase awareness |

## Agent Fleet (Org System)

The org system models a team of specialized agents stored in `.gemcode/org.json`.

### Default members

| Name | Title | Kind | Role |
|------|-------|------|------|
| kaira | BackgroundWorker | kaira_worker | Runs background jobs (tests/lint/scans) |
| verifier | Verifier | subagent | Independent review and sanity checks |

### Managing the fleet

From the REPL/TUI:
```
/agent list              — show all members
/agent tree              — show hierarchy
/agent create <name> <title> <kind> [reports_to] [address] [description]
/agent assign <member> <task>
/agent improve <member> <lessons>
```

From the agent (tools):
```
org_list()               — list members
org_hire(name, title, kind, ...)  — create a member
org_delegate(member, task, context)  — delegate work
org_spawn(name, title, kind, task)   — hire + delegate in one call
org_improve(member, lessons)  — append to member's skill
org_tree()               — show hierarchy
```

### How delegation works (two paths)

**Synchronous (ADK native `transfer_to_agent`):**
- The main agent's LLM decides to route to a sub-agent
- ADK handles the transfer natively — no custom tool call needed
- Sub-agent runs with its own tools and instruction
- Result auto-saved to session state via `output_key`
- Best for: quick reviews, exploration, verification within the same turn

**Asynchronous (mesh via `org_delegate`):**
- Agent calls `org_delegate(member, task, context)` tool
- Mesh enqueues a background job for that member
- Member runs as a full GemCode session (own workspace, own memory)
- Result flows back via event bus → fleet reports → next turn
- Best for: long-running tasks, tests, builds, parallel work

### ADK features used

| Feature | Purpose |
|---------|---------|
| `sub_agents` | Org members registered as native ADK sub-agents |
| `transfer_to_agent` | LLM-driven synchronous delegation |
| `output_key` | Auto-save agent output to session state |
| `description` | Enables LLM routing decisions |
| `AgentTool` | Available for explicit agent-as-tool invocation |
| `SequentialAgent` | Used in review pipeline |
| `ParallelAgent` | Available for concurrent workflows |
| `LoopAgent` | Available for iterative refinement |

### Skills binding

Each org member can have an assigned GemSkill (`skill_name` field). When delegated to via the mesh, their skill is automatically loaded and injected into their prompt.

## A2A Bridge (Cross-Machine Agents)

GemCode integrates with Google's Agent2Agent (A2A) protocol for cross-machine agent communication.

### Expose an agent as a network service
```
a2a_expose(member="verifier", port=8001)
```
This creates an A2A server that other GemCode instances (or any A2A-compatible framework) can connect to.

### Connect to a remote agent
```
a2a_connect(name="remote-reviewer", description="External code reviewer", agent_card_url="http://host:8001/.well-known/agent-card.json")
```
This registers the remote agent as an org member that can be delegated to like any local agent.

### List A2A-capable agents
```
a2a_list()
```

### Requirements
A2A is included by default (`google-adk[a2a]` in dependencies). No extra install needed.

## Fleet Reports (Cross-Session Persistence)

Background agent results are persisted to `.gemcode/fleet_reports.jsonl` and automatically drained into the next user turn.

### How it works
1. Mesh job completes → result appended to `fleet_reports.jsonl`
2. Next `run_turn()` → drains the file into the prompt preamble
3. Main agent sees background results without manual copy/paste

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMCODE_FLEET_REPORTS_INJECT` | 1 | Drain fleet reports into prompts |
| `GEMCODE_FLEET_REPORTS_MAX_CHARS` | 14000 | Max chars drained per turn |

## Agent Habits (Scheduled Recurring Tasks)

Habits let agents run tasks on a schedule — every N minutes, daily at a time, or via cron. They run inside the main GemCode process. (File-based **automations** under `.gemcode/automations/` are different: they are driven by **`gemcode runtime --automations`**.)

### Adding habits

From the agent (tools):
```
habits_add("test-watch", "kaira", "Run pytest -q and report failures", every_minutes=30)
habits_add("nightly-audit", "verifier", "Full security review of changed files", daily_at="02:00")
habits_add("hourly-status", "self", "Summarize git status and recent changes", cron="0 * * * *")
```

### Managing habits
```
habits_list()              — show all habits with status
habits_pause("test-watch") — stop firing until resumed
habits_resume("test-watch") — re-enable
habits_remove("test-watch") — delete permanently (stops *future* enqueues only)
habits_clear_all()         — remove every habit row (destructive)
```

### Stopping background work (habits removed but jobs still finishing)

`habits_remove` / `habits_clear_all` update **`habits.json`** only. **Queued** mesh jobs and **running** jobs (including **verifier** or other **triggers** reacting to `job.report`) continue until they finish.

To **drop the queue** and **cancel running** in-process mesh tasks:

- **REPL/TUI:** `/mesh halt` — clears queued jobs and cancels running mesh tasks.  
  `/mesh halt --habits` — same, and **empties** `.gemcode/habits.json`.
- **Tools:** `mesh_halt(clear_queued_jobs=True, cancel_running_jobs=True, remove_all_habits=False)` — set `remove_all_habits=True` to wipe all habits in one call.

Use **`mesh_status`** or **`/mesh`** (no args) for queued/running counts. This applies to the **in-process mesh** (same `gemcode` process as the TUI); it is **not** the same as **`/kaira cancel`** (runtime IPC jobs).

### Manager session: habits talking back to you

Each habit run is a **mesh job**. When it finishes, the output is written to **`.gemcode/fleet_reports.jsonl`** as a `job.report` (with **habit name** and **member** so lines read like a note from that worker).

- **While you are chatting**, **`GEMCODE_FLEET_REPORTS_AUTO_CONTINUE` defaults to on**: after each assistant response, if reports are still queued, GemCode injects a short **digest turn** so the **manager** (main session) summarizes new background output in a conversational way—without you typing “check fleet”.
- **Opt out** (save tokens): set **`GEMCODE_FLEET_REPORTS_AUTO_CONTINUE=0`** — you’ll only see habit output when it’s drained into your **next** normal message.
- **While you are idle at the prompt** (no turns running), the TUI does **not** auto-run model turns; new fleet lines accumulate until you send **any message**, run **`/fleet`** (digest) or **`/fleet show`** (peek), or finish another assistant turn (then auto-continue may chain). The TUI can print a **throttled hint** when **`job.report`** lands (`GEMCODE_FLEET_TUI_NOTIFY`). For idle wake-ups via **`gemcode runtime`**, use **`GEMCODE_FLEET_REPORTS_AUTO_CONTINUE_MODE=enqueue`** (debounced digest jobs on the fleet socket)—see [`configuration.md`](configuration.md#ui-and-behavior).

### Schedule types

| Type | Example | Meaning |
|------|---------|---------|
| `every_minutes=30` | Every 30 minutes | Interval-based |
| `every_seconds=300` | Every 5 minutes | Fine-grained interval |
| `daily_at="02:00"` | Once daily at 2am | Daily schedule |
| `cron="0 */2 * * *"` | Every 2 hours | Cron expression (M H * * *) |
| `trigger_after="test-watch"` | After another habit | Chain: fire when upstream mesh job finishes (`trigger_on`: `finished` · `failed` · `any`) |

### Habit chains (0.4.23+)

Habits can form **trigger chains**: when habit A’s mesh job completes, habit B enqueues if `trigger_after` matches A and `trigger_on` matches the outcome.

- Prompt templates may use `{{source_habit}}`, `{{source_status}}`, `{{source_report}}` / `{{report}}`.
- Cycles are rejected on add.
- Disable with `GEMCODE_HABIT_CHAINS=0`.
- Web UI: `POST /api/habits` with `trigger_after` / action `runs` for history (see [`web-ui-contract.md`](web-ui-contract.md)).

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMCODE_AGENT_HABITS` | 1 | Enable habit scheduler |
| `GEMCODE_HABITS_POLL_S` | 10 | How often to check for due habits (seconds) |
| `GEMCODE_HABIT_CHAINS` | 1 | Enable trigger-after habit chains |

### Auto-created habits (super mode)

In super mode, GemCode auto-creates habits based on project type:
- Python projects (pyproject.toml/pytest.ini): `test-watch` every 30 minutes
- Node projects (package.json): `lint-watch` every hour

## Intelligence Layer (Automatic Learning)

GemCode gets smarter with every session through three automatic learning mechanisms:

### Self-improving skills
When a skill-based delegation succeeds, the skill file gets a `## Learned pattern` section appended. Future invocations of that skill benefit from past successes. Skills evolve without manual editing.

### Proactive memory nudges
After substantial exploration (5+ files read or 3+ commands run), GemCode auto-saves key discoveries to curated memory:
- File paths explored
- Commands that worked

Future sessions start with this knowledge already loaded.

### Progressive project map
Every `list_directory`, `read_file`, and `write_file` call updates `.gemcode/project_map.json`. Over time, this builds a complete map of the project structure. Future sessions can reference this map instead of re-exploring.

### Auto-verification
After 3+ file writes or 2+ shell commands, the verifier agent auto-triggers to check for syntax errors, broken imports, and logic bugs.

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMCODE_AGENT_INTELLIGENCE` | 1 | Enable intelligence layer |
| `GEMCODE_AUTO_VERIFY` | 1 | Auto-verify after risky changes |

## Self-Healing Loop

GemCode automatically detects and fixes issues after changes — no user intervention needed.

### How it works
1. Agent makes file changes (triggers `checkpoint.created` event)
2. Self-healing detects the right verification command for the project:
   - Python: `pytest -x -q`
   - Node: `npm test` or `npm run lint`
   - Rust: `cargo check`
   - Go: `go build ./...`
   - Make: `make check` or `make test`
3. Runs verification automatically via the mesh
4. If it passes → done
5. If it fails → enqueues a fix job with the error output
6. Fix agent reads the error, applies minimal fix, re-verifies
7. Repeats up to `max_attempts` (default 2)

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMCODE_SELF_HEALING` | 1 | Enable self-healing loop |
| `GEMCODE_SELF_HEALING_MAX_ATTEMPTS` | 2 | Max fix attempts before giving up |

### Verification command override

GemCode auto-detects the verification command, but you can override it:
```bash
echo "pytest -x -q --tb=short" > .gemcode/verify_command.txt
```

## Tool Synthesis (Self-Evolving Agent)

The agent can create new reusable tools at runtime when it detects repeated patterns.

### Creating tools
```
synthesize_tool("run-tests", "Run pytest with coverage", "pytest --cov=src -q")
synthesize_tool("deploy-staging", "Deploy to staging", "git push && ssh staging 'cd app && git pull'")
synthesize_tool("check-types", "Run mypy", "mypy src/ --ignore-missing-imports")
```

### Using tools
```
run_synthesized_tool("run-tests")
run_synthesized_tool("deploy-staging")
```

### Listing tools
```
list_synthesized_tools()
```

### Storage
Tools persist in `.gemcode/synthesized_tools/` as executable scripts with companion `.json` metadata files. They survive across sessions and can be shared via version control.

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMCODE_TOOL_SYNTHESIS` | 1 | Enable tool synthesis |

## Kaira Daemon (Optional Always-On Mode)

The optional **GemCode Runtime** daemon (`gemcode runtime`) remains the right tool for always-on queues, IPC attach, and file automations; it is not required for the in-process Agent Mesh.

### When to use the daemon
- You want agents running 24/7 (not just during interactive sessions)
- You need scheduled automations (cron/interval/nightly)
- You want multiple terminals to share a job queue

### Start the daemon
```bash
gemcode runtime -C .
# or
gemcode kaira -C .
```

### Scheduled automations
```bash
gemcode runtime -C . --automations
```

Automations are configured in `.gemcode/automations/*.json`:
```json
{
  "name": "nightly-tests",
  "enabled": true,
  "prompt": "Run the full test suite and report results.",
  "triggers": [{"kind": "nightly", "at": "02:00"}]
}
```

## Where State Lives

| Path | Purpose |
|------|---------|
| `.gemcode/org.json` | Agent fleet registry |
| `.gemcode/fleet_reports.jsonl` | Background agent results |
| `.gemcode/delegation_memory.jsonl` | Delegation learning history |
| `.gemcode/triggers.json` | Self-trigger configuration |
| `.gemcode/habits.json` | Agent habits (scheduled tasks) |
| `.gemcode/project_profile.json` | Project capability profile |
| `.gemcode/project_map.json` | Progressive project structure map |
| `.gemcode/verify_command.txt` | Cached verification command (self-healing) |
| `.gemcode/awareness/structure.json` | Codebase structure graph (files, imports, exports) |
| `.gemcode/awareness/journal.jsonl` | Change journal (last 500 entries) |
| `.gemcode/awareness/insights.json` | Learned facts and correlations |
| `.gemcode/synthesized_tools/` | Agent-created reusable tools |
| `.gemcode/agents/<id>-<slug>/` | Per-agent workspaces (full GemCode sessions) |
| `.gemcode/skills/<member-name>/` | Per-member skills |
| `.gemcode/automations/*.json` | Scheduled job configs (daemon mode) |
| `.gemcode/kaira/jobs/` | Daemon job records |
| `.gemcode/ipc.sock` | Daemon IPC socket |

## Quick Start

### Super mode (fully autonomous, no questions)
```bash
gemcode -C /path/to/project --super "Fix all failing tests"
```
In super mode, GemCode:
- Enables all capabilities (memory, web search, agents, habits, triggers)
- Auto-creates default org members (kaira + verifier)
- Auto-creates habits based on project type (test-watch for Python, lint-watch for Node)
- Auto-verifies after risky changes
- Never asks for confirmation
- Agents run with full autonomy

### Normal mode (proposes, you confirm)
```bash
gemcode -C /path/to/project
```
First session: GemCode detects it's a new project and offers:
```
[gemcode] First run detected. GemCode can run autonomously with:
  • Memory (remembers across sessions)
  • Agent team (verifier + background worker)
  • Auto-verification (checks your changes)
  • Habits (scheduled recurring tasks)

  Enable autonomous mode? [Y/n]
```
Say yes → everything activates. Say no → manual control.

### With delegation
```bash
gemcode -C /path/to/project --yes
```
Then in the REPL:
```
> Analyze the auth module and fix any security issues. Delegate verification to the verifier.
```
The agent will call `org_delegate("verifier", ...)` → mesh runs verifier → result flows back.

### With always-on daemon
Terminal 1:
```bash
gemcode runtime -C . --super --automations
```
Terminal 2:
```bash
gemcode -C .
```
```
/agent assign kaira "Run the full test suite"
/kaira jobs
```
