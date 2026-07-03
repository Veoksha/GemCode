# Web UI Contract

This document captures the HTTP and streaming contract for **GemCode-compatible frontends**.

The backend is **`gemcode serve`** (built into the PyPI package). Frontends are optional separate clients — official web app, your own UI, or an editor shell — that connect to the API URL (default `http://127.0.0.1:3001`).

Use this document when:
- building or adapting a frontend for GemCode
- implementing a custom UI against `gemcode serve`
- validating `/api/chat` framing and health checks

See also:
- [Install — optional web UI](install.md)
- [CLI — `gemcode serve` / `/serve`](cli-and-repl.md#http-api-for-web-and-custom-uis-gemcode-serve)
- [Integrations — web](integrations.md#web-integration)
- [GemCode user manual](../gemcode/README.md)

## 0. Start the API

```bash
gemcode serve -C /path/to/project
# REPL/TUI alternative: /serve
```

Environment (optional):
- `GEMCODE_WEB_API_HOST` — bind host (default `127.0.0.1`)
- `GEMCODE_WEB_API_PORT` — bind port (default `3001`)
- `GEMCODE_WEB_PROJECT_ROOT` — set automatically by `gemcode serve`
- `GEMCODE_WEB_PORT_SCAN` — when the preferred port is busy, scan this many ports upward (default `30`). Port `3002` is skipped (reserved for the Next.js dev server in the reference web UI).
- `GEMCODE_WEB_SSE_KEEPALIVE_S` — SSE keepalive interval (seconds) for `/api/chat` when idle (default `20`).
- `GEMCODE_WEB_TURN_TIMEOUT_S` — optional server-side turn cap (seconds). Default `0` (no cap).
- `GEMCODE_WEB_HITL_TIMEOUT_S` — HITL approval wait timeout (seconds). Default `3600` (1 hour).
- `GEMCODE_HOSTED_TENANT_ROOT` — hosted multi-tenant workspace lock (see [`hosted.md`](hosted.md)).

## 1. Base URLs

- **Backend base URL:** where `gemcode serve` listens (default `http://127.0.0.1:3001`).
- **Frontend origin:** your web app (Next.js, Vite, etc.) — often proxies `/api/*` to the backend.
- Configure the frontend with `NEXT_PUBLIC_API_URL` (Next.js) or `VITE_API_URL` (Vite).

## 2. Health / Reachability

### 2.1 Frontend reachability checks

Compatible UIs health-check the backend in this order using `HEAD`:

1. `HEAD /api/health`
2. `HEAD /api/status`
3. `HEAD /api/chat`

Any non-5xx response is treated as “backend up”.

### 2.2 Health response (`gemcode serve`)

`GET /api/health` and `GET /api/status` return JSON including:

```json
{
  "status": "ok",
  "service": "gemcode-serve",
  "gemcode": true,
  "has_api_key": true,
  "mock_mode": false,
  "project_root": "/absolute/path/to/project",
  "cwd": "/absolute/path/to/project",
  "port": 3001,
  "url": "http://127.0.0.1:3001"
}
```

When `GEMCODE_HOSTED_TENANT_ROOT` is set, the response also includes:

```json
{
  "hosted_mode": true,
  "hosted_tenant_root": "/mnt/workspace"
}
```

Client `path` / `project_root` outside the hosted root → **403** on affected routes.

`GET /api/session` returns cwd, version, and key flags for the active project root.

If `/api/health` is missing or returns 5xx, UIs show “Backend unreachable”.

## 3. API routes (summary)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health`, `/api/status` | Reachability + config snapshot |
| GET | `/api/session` | Session/env snapshot |
| POST | `/api/chat` | Streaming chat (SSE) |
| POST | `/api/chat/approve` | HITL approval bridge |
| GET/POST | `/api/sessions` | List / name / touch ADK sessions |
| GET/POST | `/api/panel` | Git, context, tools, terminal tail, etc. |
| GET | `/api/preview` | Localhost dev-server port scan |
| GET/POST | `/api/org`, `/api/habits`, `/api/mesh` | Fleet / habits / mesh |
| GET/POST | `/api/skills`, `/api/mcp`, `/api/config` | Customization |
| GET/POST | `/api/runtime`, `/api/runtime/status`, `/api/runtime/inbox` | Runtime IPC bridge |
| POST | `/api/terminal` | Web terminal |
| POST | `/api/settings/credentials` | API key helper |
| GET | `/api/workspace/validate` | Validate project path |
| POST | `/api/workspace/pick` | Native folder picker (OS dialog) |

Implementation: `gemcode/src/gemcode/web/server.py`.

## 4. Chat: Streaming Contract

The web UI consumes streaming responses in two places. Some components use a simplified “chunk” protocol, and other hooks use a richer “StreamEvent” protocol.

### 4.1 Endpoint

- `POST /api/chat`
- Request body shape:
  - `messages`: array of `{ role, content }`
  - `model`: string model id
  - `stream`: boolean (frontend sets `stream: true`)
  - `session_id`, `project_root` (recommended)

### 4.2 Response framing

The frontend expects a streaming HTTP response (SSE-like), where each frame is:

- one or more lines starting with `data: `
- JSON payload after `data: `
- frames separated by blank lines (standard SSE `\n\n`)

### 4.3 Simplified stream protocol (“StreamChunk”)

`web/components/chat/ChatInput.tsx` imports `streamChat` from `web/lib/api.ts`.

That `streamChat` parser does:

- reads each `data: <json>` line
- `yield JSON.parse(<json>)` as a `StreamChunk`

`ChatInput` only handles these chunk types:

- `type: "text"`
  - `content` (string) is appended to the last text block
- `type: "tool_use"`
  - `tool: { id, name, input }` is appended as a `tool_use` block
- `type: "tool_result"`
  - `tool: { id, result, is_error }` is merged into the matching `tool_use` block
- `type: "done"`
- `type: "error"`
  - `error` is treated as a message

So at minimum, your GemCode `/api/chat` stream should emit StreamChunk frames with one of the above `type` values.

### 4.4 Rich stream protocol (“StreamEvent”)

`web/hooks/useChat.ts` uses `messageAPI` from `web/lib/api/messages.ts`.

That path uses `parseStream()` from `web/lib/api/stream.ts`, which expects “StreamEvent” JSON payloads.

In `web/lib/api/types.ts`, `StreamEvent.type` includes:

- `message_start`
- `content_block_start`
- `content_block_delta`
- `content_block_stop`
- `message_delta`
- `message_stop`
- `error`
- `ping`

The UI’s stream processor (`useChat.ts`) specifically cares about:

- `content_block_start` (creates a slot at `index` with either `text` or `tool_use`)
- `content_block_delta`
  - if `{ delta.type: "text_delta" }` and block is `text`, it appends `delta.text`
  - if `{ delta.type: "input_json_delta" }` and block is `tool_use`, it accumulates partial JSON into `_partialJson`
- `content_block_stop`
  - if the block is `tool_use` and `_partialJson` exists, it parses JSON into `block.input`

For best compatibility, the GemCode backend can either:

- emit only StreamChunk frames (works for `ChatInput`)
- or emit the full StreamEvent set (works for `useChat`)
- ideally emit both (so all consumers work)

