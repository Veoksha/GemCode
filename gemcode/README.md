# GemCode

Local-first coding agent: **Gemini** + **[Google ADK](https://google.github.io/adk-docs/)**, with repo tools, permissions, session persistence, and optional MCP. Implemented in clean-room fashion (reference only to third-party Claude Code trees).

## Requirements

- Python 3.11+
- A [Google AI Studio API key](https://aistudio.google.com/app/apikey) (`GOOGLE_API_KEY`)

## Install

```bash
cd gemcode
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and set `GOOGLE_API_KEY`.

## Usage

From a git repository root (or pass `-C /path/to/repo`):

```bash
gemcode "Explain the structure of src/"
gemcode --yes "Add a module docstring to src/foo.py"
gemcode --session mysess --yes "Continue: run tests and fix failures"
```

- **`--yes`**: allow mutating tools (`write_file`, `search_replace`). Shell execution is still restricted by the `.env.example` allowlist.
- **`--session`**: Conversation history is stored under `.gemcode/sessions.sqlite` (ADK `SqliteSessionService`). Reuse the same `--session` id to continue.
- **`--max-llm-calls`**: cap model↔tool iterations for this message (maps to ADK `RunConfig.max_llm_calls`). You can also set `GEMCODE_MAX_LLM_CALLS`.
- **`--model-mode`**: choose model routing mode (`auto|fast|balanced|quality`, default `fast`). In `auto`, GemCode heuristically picks a model for the prompt.
- **Gemini family routing**: set `GEMCODE_MODEL_FAMILY_MODE=auto|primary|alt`.
  - `primary` uses the `GEMCODE_MODEL*` ids (Gemini 3.x defaults)
  - `alt` uses the `GEMCODE_MODEL_ALT*` ids (Gemini 2.5 family defaults)
  - `auto` uses a cheap prompt heuristic to prefer Gemini 3.x for complex tasks and Gemini 2.5 for simpler ones.
- **Deep research**: set `--deep-research` (or `GEMCODE_ENABLE_DEEP_RESEARCH=1`) to enable research tools and route to `GEMCODE_MODEL_DEEP_RESEARCH` (default: `travel_explore`).
- Gemini 3.x tool context circulation (built-in tools + custom/function tools)
  - Enabled by default for `--deep-research` runs, so Search/URL/Maps results can be combined with your custom tools in the same workflow.
  - Controlled by `GEMCODE_TOOL_COMBINATION_MODE` / `--tool-combination-mode` (`deep_research|always|never|auto`, default: `deep_research`).
- **Embeddings**: set `--embeddings` (or `GEMCODE_ENABLE_EMBEDDINGS=1`) to enable embeddings-based semantic retrieval (and embedding-backed memory when `GEMCODE_ENABLE_MEMORY=1`).
- **Capability routing**: set `--capability-mode` (or `GEMCODE_CAPABILITY_MODE`) to `auto|research|embeddings|computer|audio|all` to enable the right toolsets (deep research / embeddings / computer-use) and route to role-appropriate models when applicable.
- **Optional compaction**: set `GEMCODE_ENABLE_COMPACT=1` to trim old `Content` entries before each model call (MVP sliding window; can break complex tool chains if too aggressive—tune `GEMCODE_MAX_CONTENT_ITEMS`).
- **Session token ceiling**: set `GEMCODE_MAX_SESSION_TOKENS` to stop the next LLM call when cumulative `usage_metadata.total_token_count` exceeds the limit.
- **Token budget tracking**: set `GEMCODE_TOKEN_BUDGET` to enforce continuation/stop decisions per user turn (token-budget audit in `.gemcode/audit.log`).
- **Stop-the-loop hooks**: set `GEMCODE_POST_TURN_HOOK=/path/to/hook.sh` (or place an executable at `.gemcode/hooks/post_turn`) to run after each user message.
- **Circuit breaker**: set `GEMCODE_MAX_CONSECUTIVE_TOOL_FAILURES` to block further tools after N consecutive tool errors.
- **Recovery-loop**: ADK `ReflectAndRetryToolPlugin`-based retries on tool failures.
  - Set `GEMCODE_ENABLE_TOOL_RECOVERY_RETRY=0` to disable.
  - Set `GEMCODE_TOOL_REFLECT_MAX_RETRIES=1` to control retries per tool.
- **Gemini thinking controls (Claude-like)**:
  - By default GemCode lets Gemini use its dynamic/adaptive thinking behavior.
  - Set `GEMCODE_DISABLE_THINKING=1` to force a best-effort “low thinking” mode:
    - Gemini 3.x: uses `thinkingLevel=minimal` (can't fully disable)
    - Gemini 2.5: uses `thinkingBudget=0` (disables thinking where supported)
  - Enable thought summaries for debugging with `GEMCODE_INCLUDE_THOUGHT_SUMMARIES=1` (increases tokens/cost).
  - Fine-tune explicitly:
    - `GEMCODE_THINKING_LEVEL=minimal|low|medium|high` (Gemini 3.x only)
    - `GEMCODE_THINKING_BUDGET=0|-1|1024` (Gemini 2.5 only)
  - Additionally, when `model_mode` is set to `fast|balanced|quality` and you
    haven't provided explicit `GEMCODE_THINKING_*` overrides:
    - Gemini 3.x auto-maps `fast|balanced|quality` to `thinkingLevel`
    - Gemini 2.5 auto-maps `fast|balanced|quality` to `thinkingBudget`
  - If `model_mode=auto`, GemCode leaves thinking unmodified.
- **Persistent memory (optional)**: set `GEMCODE_ENABLE_MEMORY=1` to ingest conversation snippets and retrieve relevant memories on future turns.
- **Project context**: optional `GEMINI.md` in the project root is injected into the system instruction.

### Permissions

- **`GEMCODE_PERMISSION_MODE=strict`**: writes and `run_command` are blocked unless the command name is in `GEMCODE_ALLOW_COMMANDS`.
- **`default`**: reads always allowed; writes require `--yes`; shell runs only allowlisted commands (see `.env.example`).

When tools are blocked by policy, the consecutive-tool circuit breaker does not count those policy rejections as “tool failures”.

Optional debugging: set `GEMCODE_EMIT_TOOL_USE_SUMMARIES=1` to write a lightweight `tool_result` record per tool into `.gemcode/audit.log`.

### Optional MCP

Install with `pip install -e ".[mcp]"` and create `.gemcode/mcp.json` (see [gemcode/mcp_loader.py](src/gemcode/mcp_loader.py)). Run with `--mcp` to attach configured servers.

### Vertex AI / Interactions API

See [src/gemcode/vertex.py](src/gemcode/vertex.py) and [src/gemcode/interactions.py](src/gemcode/interactions.py) for environment variables and future wiring.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## References (local only)

Do not commit proprietary leaked trees into this package. Keep `claude-code-leaked/` and similar folders outside version control or in a private mirror.
