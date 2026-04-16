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

Attachment access gate: when you provide attachments in interactive mode, GemCode may prompt (y/n) before it reads/uploads the attached file(s) from disk. If you answer `n`, GemCode proceeds text-only for that turn. Approval is remembered for the session.

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
