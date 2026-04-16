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
GemCode includes web compatibility surfaces and SSE helpers.

Key files:
- `docs/web-ui-contract.md`
- `gemcode/src/gemcode/web/sse_adapter.py`
- `gemcode/src/gemcode/web/web_sse_compat.py`
- `gemcode/src/gemcode/web/terminal_repl.py`

### What to document
- health checks
- `/api/chat` streaming
- chunk/event framing
- backend/frontend expectations
- terminal/web compatibility assumptions

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
