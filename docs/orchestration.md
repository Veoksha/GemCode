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
│  [Optional: Kaira Daemon for always-on server mode]                 │
│  [Optional: A2A Bridge for cross-machine agents]                    │
└─────────────────────────────────────────────────────────────────────┘
```

## Agent Mesh (In-Process Orchestration)

The Agent Mesh is the primary orchestration layer. It runs **inside** the main GemCode process — no separate daemon required.

### What it does
- Manages a priority queue of agent jobs
- Runs jobs concurrently (default: 3 parallel agents)
- Each job gets a **full-power Runner** (same as the TUI/CLI — all tools, model routing, memory, MCP)
- Results are published to the Event Bus and persisted to fleet reports
- Auto-starts on the first `run_turn()` call

### Key difference from the old system
Previously, delegation required a manually-started `gemcode runtime` daemon. Now the mesh works automatically — delegation, background work, and agent coordination all function out of the box.

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
| `agent.report` | Subtask completes | Fleet reports |
| `checkpoint.created` | Files are about to be modified | Triggers (opt-in verification) |
| `org.assign` | External delegation request | Mesh |

### How agents communicate
1. Agent A completes work → publishes `org.report` to bus
2. Trigger Engine sees the event → auto-activates Agent B (e.g., verifier)
3. Agent B runs verification → publishes its own `job.report`
4. Learning Loop records both outcomes
5. Next turn: fleet reports are drained into the user's prompt

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

### How delegation works (priority order)

1. **Agent Mesh** (primary) — always available, runs full-power agents in-process
2. **Kaira Daemon IPC** (if running) — for always-on server mode
3. **In-process subtask** (fallback) — blocking but guaranteed to work

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

## Kaira Daemon (Optional Always-On Mode)

The Kaira daemon is still available for server/always-on scenarios but is no longer required for basic orchestration.

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
| `.gemcode/project_profile.json` | Project capability profile |
| `.gemcode/agents/<id>-<slug>/` | Per-agent workspaces |
| `.gemcode/skills/<member-name>/` | Per-member skills |
| `.gemcode/automations/*.json` | Scheduled job configs |
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
