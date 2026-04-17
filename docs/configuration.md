# Configuration

## Configuration sources
GemCode configuration is assembled from:
- CLI flags
- environment variables
- `.env` files
- project-local `.gemcode/` assets
- user-wide `~/.gemcode/` assets

Primary config model:
- `gemcode/src/gemcode/config.py`

## Project root
Almost every behavior is rooted at `project_root`.

It affects:
- allowed filesystem paths
- `.gemcode/` storage location
- instruction file loading
- skills, rules, styles, hooks, OpenAPI specs, MCP config

Use `-C` deliberately.

## Environment variables
The authoritative list is in:
- `gemcode/.env.example`

Important groups:

### Model routing
- `GEMCODE_MODEL`
- `GEMCODE_MODEL_MODE`
- `GEMCODE_MODEL_FAMILY_MODE`
- `GEMCODE_MODEL_DEEP_RESEARCH`
- `GEMCODE_MODEL_AUDIO_LIVE`
- `GEMCODE_MODEL_COMPUTER_USE`

### Capabilities
- `GEMCODE_ENABLE_DEEP_RESEARCH`
- `GEMCODE_ENABLE_EMBEDDINGS`
- `GEMCODE_ENABLE_MEMORY`
- `GEMCODE_ENABLE_COMPUTER_USE`
- `GEMCODE_ENABLE_AUDIO`
- `GEMCODE_ENABLE_MAPS_GROUNDING`

### Permissions and trust
- `GEMCODE_PERMISSION_MODE`
- `GEMCODE_INTERACTIVE_PERMISSION_ASK`
- `GEMCODE_TRUST_PROMPT`
- `GEMCODE_SUPER_MODE` — when `1`/`true`/`yes`/`on`, enables [super mode](tools-and-permissions.md#super-mode-fully-autonomous) (same idea as CLI `--super` and REPL `/super`): auto-approve GemCode tool gates, skip AFC stdin tool prompt, non-interactive `get_user_choice`, etc.

### UI and behavior
- `GEMCODE_TUI`
- `GEMCODE_OUTPUT_STYLE`
- `GEMCODE_AFC_PROMPT`

### Context and budgets
- `GEMCODE_TOKEN_BUDGET`
- `GEMCODE_MAX_SESSION_TOKENS`
- compaction and policy variables from `.env.example`

## Project instruction files
GemCode loads project instructions in `gemcode/src/gemcode/agent.py`.

The current code supports:
- `gemcode.md`
- `GEMINI.md`
- `.gemcode/GEMINI.md`
- ancestor and user-global variants

For operational accuracy, document and standardize around `gemcode.md` as the primary project instruction file, while treating `GEMINI.md` as compatibility.

## The `.gemcode/` directory

### Core state
- `sessions.sqlite`
- `sessions_meta.json`
- `audit.log`
- `tool-results/`
- `artifacts/`
- `policy.json`

### Prompt assets
- `skills/`
- `output-styles/`
- `rules/`
- `hooks/`

### Integrations
- `mcp.json`
- `openapi/`
- `settings.json`

### Memory and notes
- `GEMCODE_MEMORY.md`
- `GEMCODE_USER.md`
- `memories.jsonl`
- `notes.md`
- `wal.jsonl`

## Rules and output styles

### Output styles
Locations:
- `.gemcode/output-styles/<name>.md`
- `~/.gemcode/output-styles/<name>.md`

### Rules
Locations:
- `.gemcode/rules/*.md`
- `~/.gemcode/rules/*.md`

Rules can include frontmatter path gating so they only apply to matching touched paths.

## GemSkills

### Skill locations
- `.gemcode/skills/<name>/SKILL.md`
- `~/.gemcode/skills/<name>/SKILL.md`

### Discovery behavior
GemCode preloads skill metadata and loads full bodies on demand.

Relevant code:
- `gemcode/src/gemcode/skills.py`
- `gemcode/src/gemcode/tools/skills.py`

### Frontmatter support
GemCode supports simple YAML-style frontmatter including:
- single-line scalars
- `description: >`
- `description: |`

## Hooks
Locations:
- `.gemcode/hooks/post_turn`
- `.gemcode/hooks/pre_tool_use`
- `.gemcode/hooks/post_tool_use`
- `.gemcode/hooks/session_start`
- `.gemcode/hooks/session_stop`

Hook logic:
- `gemcode/src/gemcode/hooks.py`
- plugin integration in `gemcode/src/gemcode/plugins/`

## MCP and OpenAPI

### MCP
Config file:
- `.gemcode/mcp.json`

Loader:
- `gemcode/src/gemcode/mcp_loader.py`

### OpenAPI
Spec directory:
- `.gemcode/openapi/`

Loader:
- `gemcode/src/gemcode/openapi_loader.py`

This is a first-class integration surface and should be documented alongside MCP, not as an afterthought.

## Settings and permission rules
Permission configuration can come from:
- `.gemcode/settings.json`
- `~/.gemcode/settings.json`

Permission evaluation lives in:
- `gemcode/src/gemcode/permissions.py`

This controls allow/deny patterns for tool execution, especially shell commands.

## User-wide state
GemCode also uses `~/.gemcode/` for:
- credentials
- trust metadata
- personal skills
- personal styles
- personal rules
- optional global instruction files

## Recommended configuration documentation practice
Treat these as separate layers:
1. environment and flags
2. project instruction files
3. `.gemcode/` assets
4. user-wide overrides

That separation is critical for production operators because it explains why behavior changes between repos, sessions, and machines.
