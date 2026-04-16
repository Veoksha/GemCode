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

## Attachments

### One-shot CLI attachments
Use `--attach` or `--image` for the current message only.

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

## Live audio is a separate execution mode
Use:

```bash
gemcode live-audio -C .
```

This is a streamed audio path and not a variation of the REPL/TUI shell.
