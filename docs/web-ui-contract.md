# Web UI Contract

This document captures the HTTP and streaming contract expected by GemCode-compatible web frontends and backends.

Use this document when:
- implementing a GemCode-backed API server
- adapting an existing frontend to GemCode streaming behavior
- validating `/api/chat` framing and health checks

See also:
- [GemCode user manual](../gemcode/README.md)
- [documentation index](README.md)
- [repository overview](../README.md)

## 1. Base URLs

- Frontend origin: the Next.js/Vite web app itself.
- Backend base URL: configured via `NEXT_PUBLIC_API_URL` (Next.js) or `VITE_API_URL` (Vite).
- The frontend code generally targets relative paths like `/api/chat` and relies on the web app (or Vite proxy) to reach the backend.

## 2. Health / Reachability

### 2.1 Frontend reachability checks

`web/lib/BackendContext.tsx` (under the web app) health-checks the backend in this order using `HEAD`:

1. `HEAD /api/health`
2. `HEAD /api/status`
3. `HEAD /api/chat`

Any non-5xx response is treated as “backend up”.

### 2.2 Concrete health response (reference implementation)

A typical Node reference server might expose:

- `GET /health`
- `GET /health/live`
- `GET /health/ready`
- `GET /health/startup`

Note: the UI’s reachability check targets `/api/health` (or other `/api/*` fallbacks). If your GemCode backend doesn’t implement `/api/health`, the UI will keep showing “Backend unreachable”.

## 3. Chat: Streaming Contract

The web UI consumes streaming responses in two places. Some components use a simplified “chunk” protocol, and other hooks use a richer “StreamEvent” protocol.

### 3.1 Endpoint

- `POST /api/chat`
- Request body shape (from `web/lib/api.ts`):
  - `messages`: array of `{ role, content }`
  - `model`: string model id
  - `stream`: boolean (frontend sets `stream: true`)

### 3.2 Response framing

The frontend expects a streaming HTTP response (SSE-like), where each frame is:

- one or more lines starting with `data: `
- JSON payload after `data: `
- frames separated by blank lines (standard SSE `\n\n`)

### 3.3 Simplified stream protocol (“StreamChunk”) used by `ChatInput`

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

### 3.4 Rich stream protocol (“StreamEvent”) used by `useChat`

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

