# Capabilities

## Capability model
Capabilities are optional bundles of tools, prompts, and routing behavior that can be enabled through flags, environment variables, or prompt heuristics.

Relevant code:
- `gemcode/src/gemcode/capability_routing.py`
- `gemcode/src/gemcode/model_routing.py`
- `gemcode/src/gemcode/modality_tools.py`
- `gemcode/src/gemcode/config.py`

## Deep research

### What it enables
- research-oriented tool routing
- search and URL context tools
- optional maps grounding
- optional model routing to a dedicated deep-research model

### Configuration
- `--deep-research`
- `GEMCODE_ENABLE_DEEP_RESEARCH`
- `GEMCODE_MODEL_DEEP_RESEARCH`

### Important operational note
If no dedicated deep-research model is configured, GemCode should safely fall back to the main model.

### Common user issue
A stale or invalid deep-research model id can cause runtime failures such as:
- model not found

## Embeddings

### What it enables
- semantic file search
- optional embedding-backed memory retrieval

### Configuration
- `--embeddings`
- `GEMCODE_ENABLE_EMBEDDINGS`
- `GEMCODE_EMBEDDINGS_MODEL`

## Memory

### Two main memory surfaces
1. curated memory
2. retrieval memory

### Curated memory
Files:
- `.gemcode/GEMCODE_MEMORY.md`
- `.gemcode/GEMCODE_USER.md`

Purpose:
- stable, human-approved facts
- lightweight prompt injection

### Retrieval memory
Backing file:
- `.gemcode/memories.jsonl`

Implementations:
- keyword/file memory
- embedding-backed memory

Code:
- `gemcode/src/gemcode/memory/file_memory_service.py`
- `gemcode/src/gemcode/memory/embedding_memory_service.py`

## VeoMem
GemCode can also load optional wake-up context from VeoMem.

Relevant integration points:
- `gemcode/src/gemcode/veomem_bridge.py`
- `veomem/`
- wake-up integration in `gemcode/src/gemcode/session_runtime.py`

This is a higher-level recall/context surface and should be documented separately from curated memory and retrieval memory.

## Browser automation and computer use

### What it enables
- browser automation
- screenshot and text extraction helpers
- ADK computer-use tools when available

### Configuration
- `GEMCODE_ENABLE_COMPUTER_USE`
- capability-mode overrides
- Playwright/browser prerequisites

### Important operational note
If Playwright is unavailable, GemCode disables computer use for the session and should stay on a normal model path.

## Live audio

### Entry
```bash
gemcode live-audio -C .
```

### What it does
- captures microphone input
- sends it to Gemini Live
- reuses the same runner/agent/tool assembly model where practical

### Important distinction
Live audio is not the REPL or TUI. It is a separate streaming interaction mode.

### Status
This capability is currently **experimental** and may be unavailable or unreliable depending on Gemini Live service behavior (for example transient `1011` internal errors). Treat it as **future scope** for production workflows.

## Super mode (cross-cutting)

Super mode is not a separate capability bundle; it changes **permission and UX behavior** across the normal tool surface (CLI, REPL/TUI, Kaira). It enables maximum autonomy: auto-approved mutations/shell, no GemCode HITL, skipped AFC stdin prompt, and automatic `get_user_choice` (first option).

See:
- [`tools-and-permissions.md`](tools-and-permissions.md#super-mode-fully-autonomous)
- [`cli-and-repl.md`](cli-and-repl.md) — `--super`, `/super`, `gemcode runtime --super` (alias: `gemcode kaira --super`)

## Capability routing behavior
Capability routing is applied before runner construction and can:
- enable research tools
- enable embeddings
- steer model routing

It does not exist only for UX convenience; it materially changes the available tool inventory and therefore the execution contract.

## AFC and capabilities
Some capabilities add non-callable toolsets, which can affect Automatic Function Calling.

This matters especially for:
- MCP
- OpenAPI
- some built-in toolset integrations

## Recommended documentation pattern
For every capability, production docs should state:
- how to enable it
- what tools it adds
- what model routing it influences
- prerequisites
- common failure modes
- how it interacts with permissions and AFC
