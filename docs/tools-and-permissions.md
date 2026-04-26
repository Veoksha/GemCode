# Tools and Permissions

## Tool architecture
GemCode exposes tools through ADK, but the tool inventory is not homogeneous.

Tool sources include:
- Python callables
- ADK built-in tools
- ADK toolsets
- browser/computer-use toolsets
- MCP toolsets
- OpenAPI-generated toolsets

This mixed tool model is important because behavior such as Automatic Function Calling can depend on the active tool list.

Core registration paths:
- `gemcode/src/gemcode/tools/__init__.py`
- `gemcode/src/gemcode/session_runtime.py`
- `gemcode/src/gemcode/modality_tools.py`

## Core tool families

### Planning and orchestration
- todo management
- explicit thinking helpers
- subtask spawning

Relevant code:
- `gemcode/src/gemcode/tools/todo.py`
- `gemcode/src/gemcode/tools/think.py`
- `gemcode/src/gemcode/tools/run_subtask.py`

### Filesystem and search
- file reads
- directory listing
- globbing
- grep-style search
- repo map

These are read-only discovery tools used heavily before any mutation.

### Mutations
- file writes
- search/replace
- file moves
- file deletion
- notebook edits

These are permission-gated and trust-gated.

### Shell and processes
- one-shot allowlisted commands
- richer shell commands and pipelines
- background jobs
- task inspection and termination

Important distinction:
- `run_command` is stricter and more structured
- `bash` is more flexible and can run background commands

### Web tools
- search
- fetch

These are gated by policy and capability configuration.

### Memory and skill tools
- curated memory read/write helpers
- skill discovery
- skill loading
- memory retrieval when enabled

### Checkpoints and recovery
- checkpoint listing
- checkpoint undo
- offloaded tool-result retrieval

## Modality and external tools

### Deep research and semantic tools
Added by:
- `gemcode/src/gemcode/modality_tools.py`

Examples:
- web/search built-ins
- URL context
- semantic search
- optional maps grounding

### Browser and computer use
Added by:
- `gemcode/src/gemcode/session_runtime.py`
- `gemcode/src/gemcode/tools/browser.py`
- `gemcode/src/gemcode/computer_use/browser_computer.py`

### MCP
Loaded from `.gemcode/mcp.json`.

### OpenAPI
Loaded from `.gemcode/openapi/`.

## Permission layers
There are multiple layers of execution control:

1. workspace trust
2. permission mode
3. allow/deny settings
4. `--yes` and HITL approval
5. tool-specific guardrails

### Workspace trust
Without trust, filesystem and shell mutation surfaces are blocked even if the user passes permissive flags.

Code:
- `gemcode/src/gemcode/trust.py`

### Permission modes
Configured by:
- `GEMCODE_PERMISSION_MODE`
- CLI approval flags

Modes include at least:
- default
- strict

### Interactive approval
Use:
- `--interactive-ask`
- `GEMCODE_INTERACTIVE_PERMISSION_ASK`

GemCode can prompt in-run for sensitive operations instead of assuming blanket approval.

#### Prompt timing (immediate)
When a tool call requires approval, GemCode will surface the **Y/N** approval prompt as soon as the model requests confirmation (ADK `request_confirmation`). The UI will not continue streaming extra “thinking” text before asking — it pauses promptly so you can approve/deny without waiting.

### Super mode (fully autonomous)
Use when you want **no human-in-the-loop** for GemCode’s own gates. Super mode does **not** remove OS-level prompts (for example macOS file access); it removes GemCode’s interactive approval layers listed below.

**What it turns off or auto-approves**

1. **Mutations and shell** — same as `--yes`: `before_tool` allows `write_file`, `search_replace`, `run_command`, `bash`, computer-use tools, etc. (`gemcode/src/gemcode/callbacks.py`).
   - This includes **fleet/orchestration mutations** like `org_delegate`, `org_hire`, `org_spawn`, `automations_run`, etc.
2. **ADK `request_confirmation` handoffs** — auto-confirmed in the one-shot CLI (`invoke.py`), scrollback TUI, and Kaira job runner when `yes_to_all` / super mode applies (so runs do not block on stdin or IPC approval).
3. **AFC tool-mode stdin prompt** — pre-selects **all tools** (`_afc_choice=all`), skipping the `afc>` prompt (`session_runtime.py`).
4. **Attachment read/upload gate** — treated like `--yes` for the session (`_attachments_allowed`).
5. **Workspace trust prompt (CLI)** — on first start in a TTY, the project root is trusted without `y/N` (`cli.py`).
6. **TUI-only extras** — Kaira IPC “approve tool?” and manager “enqueue fix job?” paths auto-approve when super/`--yes` (`tui/scrollback.py`).
7. **`get_user_choice`** — GemCode swaps ADK’s long-running UI tool for a plain tool that returns the **first non-empty** option in `options`. Put your preferred default first. Implementation: `gemcode/src/gemcode/tools/user_choice.py` (root agent and sub-agents via `agent.py` / `tools/subtask.py`).

**How to enable**

- One-shot / REPL: `gemcode --super …` or `GEMCODE_SUPER_MODE=1 gemcode …`
- REPL/TUI after start: `/super` (use `/super off` to clear the `super_mode` flag only; it does not restore prior `yes_to_all` / HITL defaults)
- Runtime daemon: `gemcode runtime -C . --super` (alias: `gemcode kaira -C . --super`)

**Configuration**

- Env: `GEMCODE_SUPER_MODE=1` (see `gemcode/.env.example`)
- Code applies policy in `apply_super_mode()` — `gemcode/src/gemcode/config.py`

**Runtime note:** Tool lists are fixed when the `LlmAgent` is built. If you toggle `/super` mid-session, you may need a **new session** or **restart** so `get_user_choice` and other registrations match the new mode.

### Seeing “agent work” in real time
There are two relevant execution styles:

- **Runtime-backed agents (`kaira_worker`)**: jobs stream `job_*` events over IPC and will publish `org.report` on completion by default.
- **In-process subagents (`run_subtask` / `spawn_subtasks`)**: the parent emits `agent.report` lifecycle events; for a raw stream, attach to the runtime IPC (when running) with `gemcode runtime attach -C .`.

**Safety:** this is intentionally dangerous on untrusted codebases. Prefer `--yes`, `/trust`, and optional `--interactive-ask` when you want guardrails.

Attachment access gate: when you provide attachments in an interactive TTY session, GemCode prompts (y/n) before it reads/uploads the attached file(s) from disk. If you answer `n`, GemCode proceeds text-only for that turn. Approval is remembered for the session.

You can disable this prompt with `GEMCODE_ATTACHMENTS_ASK=0`.

### Settings-based allow/deny
Config files:
- `.gemcode/settings.json`
- `~/.gemcode/settings.json`

Evaluator:
- `gemcode/src/gemcode/permissions.py`

## Background processes
`bash` can run jobs in the background. Background lifecycle tools let the agent:
- list running tasks
- read task output
- terminate tasks

This is especially important for long-running servers, test watchers, and scheduled work.

## Tool-result offloading
Large tool outputs can be offloaded into `.gemcode/tool-results/` and replaced in context by stable references such as:
- `tool_result:<sha256>`

This reduces prompt size and preserves retrievability.

Code:
- `gemcode/src/gemcode/tool_result_store.py`

## IDE-specific behavior
In IDE mode, not every tool mutates directly.

Some operations become:
- edit proposals
- command suggestions
- permission requests

This behavior is implemented in:
- `gemcode/src/gemcode/ide_stdio.py`
- `gemcode/src/gemcode/tools/edit.py`
- `gemcode/src/gemcode/tools/shell.py`
- `gemcode/src/gemcode/tools/bash.py`

## Automatic Function Calling
Gemini AFC may be degraded or disabled when non-callable toolsets are present.

GemCode can prompt the user to choose between:
- all tools
- callable-only tools

Current user-facing configuration:
- `GEMCODE_AFC_PROMPT`

Runtime assembly:
- `gemcode/src/gemcode/session_runtime.py`

## Safety documentation principles
Production docs for tools should always explain:
- what the tool family does
- whether it mutates state
- which approval layer applies
- whether it behaves differently in IDE mode
- how its output is stored or offloaded
