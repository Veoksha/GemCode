# Integrations

## IDE integration

### Entry point
```bash
gemcode ide --stdio
```

### Purpose
This runs the editor bridge over JSONL stdin/stdout for editor clients.

Core files:
- `gemcode/src/gemcode/ide_stdio.py`
- `gemcode/src/gemcode/ide_protocol.py`

### Key behavior
- requests are read as JSONL
- inline attachments may be materialized into temporary files
- turns flow through the same core `run_turn()` pipeline
- mutating operations may become proposals rather than direct edits

### Why this matters for docs
The IDE protocol is not just “chat over stdio”; it has different execution semantics and requires a dedicated integration contract.

## Web integration
GemCode includes a built-in HTTP API for web and custom frontends.

### Entry point
```bash
gemcode serve -C /path/to/project
```

From the REPL/TUI: `/serve`, `/serve status`, `/serve stop`, `/serve url`.

### Purpose
- expose the same agent, tools, and sessions over HTTP
- let any UI (official web app, internal dashboard, editor shell) connect without bundling GemCode into the frontend repo
- default bind: `http://127.0.0.1:3001` (auto-falls back to the next available port if busy)

### Key files
- `docs/web-ui-contract.md` — routes, health, SSE framing
- `gemcode/src/gemcode/web/server.py` — HTTP server (`gemcode serve`)
- `gemcode/src/gemcode/web/serve_state.py` — background `/serve` process tracking
- `gemcode/src/gemcode/web/sse_adapter.py` — chat streaming subprocess
- `gemcode/src/gemcode/web/*_api.py` — panel, sessions, preview, org/mesh, runtime, terminal, etc.

### What to document
- `gemcode serve` and `/serve`
- health checks (`GET /api/health`)
- `/api/chat` streaming (SSE)
- HITL approve (`POST /api/chat/approve`)
- optional `GEMCODE_WEB_API_HOST` / `GEMCODE_WEB_API_PORT`
- hosted multi-tenant: `GEMCODE_HOSTED_TENANT_ROOT`, `/hosted`, [`hosted.md`](hosted.md), `deploy/gcp/`

Frontends live in separate repositories and only need the API URL.

## MCP integration

### Config
- `.gemcode/mcp.json`

### Loader
- `gemcode/src/gemcode/mcp_loader.py`

### Behavior
MCP toolsets are loaded into the runner assembly path and become part of the tool inventory for the session.

### Docs requirement
Document:
- config shape
- connection types
- install requirements
- AFC implications

## OpenAPI integration

### Config location
- `.gemcode/openapi/`

### Loader
- `gemcode/src/gemcode/openapi_loader.py`

### Behavior
OpenAPI specs can become REST-backed tools automatically.

### Why this needs dedicated docs
OpenAPI is currently as important as MCP from a runtime perspective and should be documented as a first-class integration surface.

## Browser/computer integration

### Components
- Playwright-backed browser computer
- browser inspection helpers
- computer-use toolsets

Relevant code:
- `gemcode/src/gemcode/computer_use/browser_computer.py`
- `gemcode/src/gemcode/tools/browser.py`
- `gemcode/src/gemcode/session_runtime.py`

## Skills as an integration surface
GemSkills are not just prompt files; they are a reusable integration layer for:
- operational playbooks
- domain workflows
- repo-local standards

Relevant code:
- `gemcode/src/gemcode/skills.py`
- `gemcode/src/gemcode/tools/skills.py`

## Integration documentation standard
Every integration page should answer:
- how to enable it
- what files/config it needs
- what runtime path it plugs into
- what changes in permissions or tool behavior
- what errors users commonly see
