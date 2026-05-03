# CLI, REPL, and TUI

## Execution modes

### One-shot CLI
Use when you want a single prompt/response cycle:

```bash
gemcode -C /path/to/project "Explain this codebase"
```

Flow:
- parse args in `gemcode/src/gemcode/cli.py`
- build config
- route model/capabilities
- create runner
- run one turn
- print final text

### REPL
Use when you want an interactive session:

```bash
gemcode -C /path/to/project
```

The REPL:
- keeps a session id alive
- reuses a runner
- supports slash commands
- can upgrade to the TUI when supported

### TUI (GemCode terminal UI)
GemCode ships **one** terminal UI: a scrollback-style interactive session in:
- `gemcode/src/gemcode/tui/scrollback.py`
- `gemcode/src/gemcode/tui/input_handler.py`

There is no separate fullscreen TUI package in the repo.

Enablement is controlled by `GEMCODE_TUI` (default on in a TTY). When active, it provides:
- native terminal scrollback (no alt-screen app)
- slash completion (via prompt_toolkit when available)
- integrated status, tool display, and optional **GemCode Runtime** job/event stream (Unix socket)
- the same **fleet report inbox** drain as `run_turn`: completed background reports in `.gemcode/fleet_reports.jsonl` are prepended each turn when `GEMCODE_FLEET_REPORTS_INJECT=1` (see [`orchestration.md`](orchestration.md#fleet-report-inbox--auto-continue-hands-off-summaries))

TUI also shows a live one-line spinner with timing while work runs:
**Thinking… → Running… → Querying…** (e.g. `Running… (42s)`). These timers should
continue updating even during long shell tools (tests/builds) because shell tools
run off the UI loop (threaded/async) rather than blocking the TUI event loop.

## Main CLI invocations

### Basic prompt
```bash
gemcode -C . "Summarize the authentication flow"
```

### Allow mutating work
```bash
gemcode -C . --yes "Fix the failing tests"
```

### Super mode (fully autonomous, no GemCode HITL)
```bash
gemcode -C . --super "Refactor the module and run tests"
# or
GEMCODE_SUPER_MODE=1 gemcode -C . "Large autonomous task"
# background jobs (same project): prefer --super so jobs never block on confirmations
gemcode runtime -C . --super
```

In the REPL/TUI, run `/super` once to enable (or `/super off` to clear the flag only).

Super mode implies `--yes`, skips the AFC `afc>` stdin prompt (keeps all toolsets), auto-trusts the workspace on first interactive CLI start, auto-approves ADK confirmation handoffs and (in the TUI) runtime IPC approvals, and replaces `get_user_choice` with **first-option** auto-selection. Full list: [`tools-and-permissions.md`](tools-and-permissions.md#super-mode-fully-autonomous).

**Note:** If you turn `/super` on after the session started, rebuild the agent (new session / restart) if you rely on the non-interactive `get_user_choice` tool.

### Ask before writes
```bash
gemcode -C . --interactive-ask "Update the docs"
```

### Attach files to a one-shot turn
```bash
gemcode -C . --attach ./report.pdf "Summarize this"
gemcode -C . --image ./ui.png "What is wrong with this screen?"
```

### Attach REPL/TUI to an existing runtime (IPC)
Use when a **`gemcode runtime`** is already running and you want this session to use the same fleet socket (discovery order matches [`configuration.md`](configuration.md#ui-and-behavior): `manager_ipc.txt`, default `.gemcode/ipc.sock`, then `GEMCODE_KAIRA_SOCKET` if that path exists):

```bash
gemcode -C /path/to/fleet/root --connect /path/to/.gemcode/ipc.sock
```

Implementation sets `GEMCODE_KAIRA_SOCKET` and `GEMCODE_KAIRA_AUTO_CONNECT` for this process (`gemcode/src/gemcode/cli.py`).

## REPL behavior

### Prompt loop
The REPL reads one line at a time, then:
- exits on `quit`, `exit`, `:q`, or `/exit`
- routes slash commands through `repl_slash.py`
- otherwise sends the line through the model turn pipeline

### Slash commands
Slash command metadata and help live in:
- `gemcode/src/gemcode/repl_commands.py`
- `gemcode/src/gemcode/repl_slash.py`

Important families:
- context and project setup
- session management
- model and capability control
- diagnostics
- GemSkills
- checkpoints and rewind
- eval and autotune

### Slash completion
The canonical command registry is in `repl_commands.SLASH_COMMANDS`.

Completion behavior differs slightly by shell/TUI layer, but the source of truth is shared.

## Slash command reference

The canonical command list is defined in `gemcode/src/gemcode/repl_commands.py`.

| Command | Purpose |
|---|---|
| `/add-dir` | Extra read/search roots · `/add_dir` works too |
| `/append` | Iterate a file · `/append gemskill <name> <request>` |
| `/audit` | Tail `audit.log` · `/logs` same |
| `/autotune` | Branch + eval ledger · `/autotune init <tag>` · `/autotune eval` |
| `/batch` | Built-in batch GemSkill (large parallel changes) |
| `/caveman` | Terse output mode · `/caveman` lite, full, ultra, wenyan, off |
| `/caveman:compress` | Compress memory file · `/caveman:compress <path> [lite|full|ultra]` |
| `/budget` | Per-turn token budget · `/token-budget` same |
| `/caps` | Capabilities · `/capabilities` / `/capability` same |
| `/clear` | Fresh session · alias of `/session new` (not listed in Tab completion registry; still supported) |
| `/code` | Toggle ADK BuiltInCodeExecutor (sandboxed Python) |
| `/compact` | Context compaction / summarization |
| `/summarise` | Persist a durable session summary + key facts, then start a fresh session |
| `/computer` | Browser automation · `/browser` same |
| `/config` | Dump active configuration |
| `/context` | Context pressure + token breakdown |
| `/cost` | Session token usage + estimated cost |
| `/create` | New GemSkill file · `/create gemskill <name> [description]` |
| `/gemskill` | Load skill into session prompt · `/gemskill <name>` · list · clear |
| `/curated` | Curated memory snapshot · `/memory-files` / `/memoryfiles` same |
| `/diff` | Git diff or checkpoint diff |
| `/doctor` | Environment sanity check |
| `/embeddings` | Semantic file search · `/embed` same |
| `/eval` | Eval gates (tools + pytest) · `/eval llm` optional |
| `/exit` | Leave the REPL · `/quit` same |
| `/fleet` | Fleet inbox — `/fleet` digest · `/fleet show` peek · `/fleet help` (habits / mesh `job.report` lines) |
| `/help` | Short help · `/?` same |
| `/hooks` | Post-turn hook configuration |
| `/attach` | Queue file(s) for next message (PDF, images, …) · `/image` / `/file` / `/img` · list · clear |
| `/init` | Generate `gemcode.md` project instructions |
| `/file` | Alias of `/attach` |
| `/image` | Alias of `/attach` (same queue) |
| `/img` | Alias of `/attach` |
| `/runtime` | Fleet manager socket status · how to start **`gemcode runtime`** · attach/connect hints (`gemcode/src/gemcode/repl_slash.py`) |
| `/bus` | Publish/subscribe lightweight messages on the runtime IPC bus |
| `/inbox` | Filters for which bus topics/addresses this UI displays |
| `/kaira` | Background job scheduler help · **`gemcode runtime`** preferred (`gemcode kaira` is an alias) |
| `/kaira jobs` | List recent runtime jobs via IPC (daemon reachable) |
| `/kaira job <id>` | Show a single job record via IPC |
| `/kaira cancel <id>` | Cancel a job via IPC |
| `/kaira follow <id>` | (TUI) Only show events for one job id prefix |
| `/kaira unfollow` | (TUI) Clear the follow filter |
| `/automations` | Local scheduled automations (requires **GemCode Runtime** IPC when enqueueing) + heartbeat |
| `/automations list` | List `.gemcode/automations/*.json` |
| `/automations init <name>` | Create starter automation config |
| `/automations run <name>` | Enqueue an automation now via runtime IPC |
| `/automations heartbeat <seconds> [prompt...]` | Heartbeat job interval + prompt |
| `/afc` | AFC prompt defaults (`GEMCODE_AFC_DEFAULT`, `GEMCODE_AFC_PROMPT`) |
| `/limits` | Execution limits (calls, context, …) |
| `/live-audio` | How to run `gemcode live-audio` · `/liveaudio` same |
| `/login` | How to run `gemcode login` (API key) |
| `/maps` | Maps grounding · `/maps` on/off · `/map` same |
| `/mesh` | In-process agent mesh — `/mesh status` · `/mesh halt` · `/mesh halt --habits` · `/mesh help` |
| `/memory` | Persistent memory · `/memory` on/off |
| `/mode` | Model mode: fast, balanced, quality, auto |
| `/model` | Model info / override · `/models` same |
| `/notes` | `.gemcode/notes.md` · `/notes clear` · `/notes edit` |
| `/permissions` | Permission + HITL · `/perm` / `/permission` same |
| `/plan` | Plan-before-act mode |
| `/research` | Deep research tools · `/research` on/off |
| `/review` | Parallel code review |
| `/rewind` | Checkpoints · `/checkpoint` same |
| `/rules` | Rule files from `.gemcode/rules/` |
| `/session` | Session id / list / resume / new |
| `/skill` | Load or show a GemSkill |
| `/skills` | List GemSkills |
| `/status` | Model, capabilities, thinking, limits |
| `/style` | Output styles · `/style <name>` or off |
| `/super` | Super mode: auto-approve tools/shell, no HITL · `/super off` |
| `/thinking` | Thinking verbose/brief/off, budget, level |
| `/tools` | Tool inventory · `/tools smoke` |
| `/trust` | Workspace trust · `/trust` on/off |
| `/version` | GemCode version |
| `/agent` | Agent registry + workspaces (create/list/tree/status/assign/spawn/improve/send/trigger). **Alias:** `/agents` (same parser) |

## Attachments

### One-shot CLI attachments
Use `--attach` or `--image` for the current message only.

In interactive runs (TTY), GemCode may prompt once per session:
`Allow GemCode to read and upload the attached file(s) from disk? (y/n)`
If you answer `n`, GemCode will proceed text-only for that turn.

If you want to disable this prompt, set `GEMCODE_ATTACHMENTS_ASK=0`.

### REPL attachments
Use:

```text
/attach ./file.pdf
```

Then send the next message.

Aliases:
- `/image`
- `/img`
- `/file`

GemCode also supports inline-prompt attachment usage in REPL/TUI attachment commands.

## Session behavior

### Session ids
Use `--session <id>` to continue a prior conversation.

### Session commands
- `/session`
- `/session list`
- `/session name <name>`
- `/session resume <id-or-name>`
- `/session new`
- `/clear`

Session storage is backed by:
- `.gemcode/sessions.sqlite`
- `.gemcode/sessions_meta.json`

## Cost and context
Useful REPL commands:
- `/cost`
- `/status`
- `/context`
- `/compact`
- `/summarise`
- `/budget`
- `/limits`

These surface runtime telemetry around:
- token usage
- estimated cost
- context pressure
- compaction behavior
- model/capability settings

## AFC prompt behavior
Gemini Automatic Function Calling (AFC) can be affected by non-callable toolsets such as MCP or OpenAPI toolsets.

By default GemCode **does not** show the `afc>` prompt: it keeps **all tools** (set **`GEMCODE_AFC_PROMPT=1`** to ask, or use `/afc` in the REPL). Implementation: `gemcode/src/gemcode/session_runtime.py`.

## GemCode Runtime is not the TUI
**GemCode Runtime** (`gemcode runtime`; alias `gemcode kaira`) is a queued background scheduler with a Unix-socket control plane, not the GemCode TUI.

If you want the TUI, use:

```bash
gemcode -C .
```

## Watch runtime + bus traffic (another terminal)
If you want to see *everything running* (job lifecycle events and bus messages) from a separate terminal, attach to the runtime IPC stream:

```bash
gemcode runtime attach -C .
```

This prints a raw JSONL stream (universal for piping into other tools). The GemCode TUI renders a human-friendly subset by default.

### One-terminal mode (TUI + embedded runtime)
If you want continuous background jobs **and** you want to see everything in the same terminal UI, you can run a headless runtime inside the TUI process:

```bash
GEMCODE_TUI_WITH_KAIRA=1 gemcode -C .
```

In this mode, GemCode starts a headless **GemCode Runtime** (IPC-only; env name is historical) and the TUI auto-subscribes to job events so background output is printed inline.

### Scheduled automations (local)
The runtime can also run simple local scheduled automations (like “hourly”, “nightly”, or cron-style triggers) from:

- `.gemcode/automations/*.json`

Enable them when running the runtime:

```bash
gemcode runtime -C . --automations
```

Optional “heartbeat” jobs (enqueue a prompt every N seconds):

```bash
gemcode runtime -C . --heartbeat-every-s 240 --heartbeat-prompt "Heartbeat: summarise XAUUSD status"
```

If you want a queue-driven scheduler, use:

```bash
gemcode runtime -C .
```

## Orchestration (GemCode Runtime + Org + parallel agents)

GemCode supports “organisation-style” delegation and background work:

- **Runtime (daemon)**: a priority-queue scheduler with an IPC control plane and event stream (`gemcode runtime`).
- **Agent fleet**: persistent members under `.gemcode/org.json` with role skills under `.gemcode/skills/` and workspaces under `.gemcode/agents/`.
- **Parallel subtasks**: the model can run isolated subtasks concurrently (`spawn_subtasks`).

How to use (start here):
- `docs/orchestration.md`

## Live audio is a separate execution mode
Use:

```bash
gemcode live-audio -C .
```

This is a streamed audio path and not a variation of the REPL/TUI shell.

Status note:
- This mode is currently **experimental** and may fail due to upstream Gemini Live availability/reliability (e.g. transient `1011` internal errors).
- Treat `live-audio` as **future scope** for production usage.
