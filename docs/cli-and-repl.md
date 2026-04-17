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

### TUI
The TUI is the scrollback-style terminal UI implemented in:
- `gemcode/src/gemcode/tui/scrollback.py`
- `gemcode/src/gemcode/tui/input_handler.py`

Enablement is controlled by `GEMCODE_TUI`. When active, it provides:
- scrollback rendering
- richer layout
- slash completion
- integrated status and tool display

## Main CLI invocations

### Basic prompt
```bash
gemcode -C . "Summarize the authentication flow"
```

### Allow mutating work
```bash
gemcode -C . --yes "Fix the failing tests"
```

### Ask before writes
```bash
gemcode -C . --interactive-ask "Update the docs"
```

### Attach files to a one-shot turn
```bash
gemcode -C . --attach ./report.pdf "Summarize this"
gemcode -C . --image ./ui.png "What is wrong with this screen?"
```

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
| `/clear` | Fresh session · same as `/session new` |
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
| `/help` | Short help · `/?` same |
| `/hooks` | Post-turn hook configuration |
| `/attach` | Queue file(s) for next message (PDF, images, …) · `/image` / `/file` / `/img` · list · clear |
| `/init` | Generate `GEMINI.md` project instructions |
| `/file` | Alias of `/attach` |
| `/image` | Alias of `/attach` (same queue) |
| `/img` | Alias of `/attach` |
| `/kaira` | Background job scheduler help · how to run `gemcode kaira` |
| `/kaira jobs` | List recent Kaira jobs via IPC (when daemon is running) |
| `/kaira job <id>` | Show a single job record via IPC |
| `/kaira cancel <id>` | Cancel a job via IPC |
| `/kaira follow <id>` | (TUI) Only show events for one job id prefix |
| `/kaira unfollow` | (TUI) Clear the follow filter |
| `/limits` | Execution limits (calls, context, …) |
| `/live-audio` | How to run `gemcode live-audio` · `/liveaudio` same |
| `/login` | How to run `gemcode login` (API key) |
| `/maps` | Maps grounding · `/maps` on/off · `/map` same |
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
| `/thinking` | Thinking verbose/brief/off, budget, level |
| `/tools` | Tool inventory · `/tools smoke` |
| `/trust` | Workspace trust · `/trust` on/off |
| `/version` | GemCode version |
| `/org` | Org chart commands (list/tree/hire/assign/spawn/improve) |
| `/hire` | Alias of `/org hire` |
| `/delegate` | Alias of `/org assign` |
| `/assign` | Alias of `/org assign` |
| `/spawn` | Alias of `/org spawn` |

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

In interactive mode, GemCode can ask whether to:
- keep **all tools**
- restrict to **callable-only tools**

This behavior is implemented in `gemcode/src/gemcode/session_runtime.py`.

## Kaira is not the TUI
`gemcode kaira` is a queued background scheduler, not the scrollback UI.

If you want the TUI, use:

```bash
gemcode -C .
```

If you want a queue-driven scheduler, use:

```bash
gemcode kaira -C .
```

## Orchestration (Kaira + Org + parallel agents)

GemCode supports “organisation-style” delegation and background work:

- **Kaira (daemon)**: a priority-queue scheduler with an IPC control plane and event stream.
- **Org chart**: persistent members under `.gemcode/org.json` with role skills under `.gemcode/skills/`.
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
