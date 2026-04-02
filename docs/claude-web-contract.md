# Claude Web UI Contract (GemCode-Mirroring)

This document captures the HTTP/WebSocket contract used by `claude-code-leaked/web` so we can implement a GemCode-backed backend that behaves the same way.

## 1. Base URLs

- Frontend origin: the Next.js/Vite web app itself.
- Backend base URL: configured via `NEXT_PUBLIC_API_URL` (Next.js) or `VITE_API_URL` (Vite).
- The frontend code generally targets relative paths like `/api/chat` and relies on the web app (or Vite proxy) to reach the backend.

## 2. Health / Reachability

### 2.1 Frontend reachability checks

`claude-code-leaked/web/lib/BackendContext.tsx` health-checks the backend in this order using `HEAD`:

1. `HEAD /api/health`
2. `HEAD /api/status`
3. `HEAD /api/chat`

Any non-5xx response is treated as “backend up”.

### 2.2 Concrete health response (Claude backend)

The backend implementation in `claude-code-leaked/src/server/api/index.ts` provides:

- `GET /health`
- `GET /health/live`
- `GET /health/ready`
- `GET /health/startup`

Note: the Claude UI’s reachability check targets `/api/health` (or other `/api/*` fallbacks). If your GemCode backend doesn’t implement `/api/health`, the UI will keep showing “Backend unreachable”.

## 3. Chat: Streaming Contract

The web UI consumes streaming responses in two places. Some components use a simplified “chunk” protocol, and other hooks use a richer “StreamEvent” protocol.

### 3.1 Endpoint

- `POST /api/chat`
- Request body shape (from `claude-code-leaked/web/lib/api.ts`):
  - `messages`: array of `{ role, content }`
  - `model`: string model id
  - `stream`: boolean (frontend sets `stream: true`)

### 3.2 Response framing

The frontend expects a streaming HTTP response (SSE-like), where each frame is:

- one or more lines starting with `data: `
- JSON payload after `data: `
- frames separated by blank lines (standard SSE `\n\n`)

### 3.3 Simplified stream protocol (“StreamChunk”) used by `ChatInput`

`claude-code-leaked/web/components/chat/ChatInput.tsx` imports `streamChat` from `claude-code-leaked/web/lib/api.ts`.

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

`claude-code-leaked/web/hooks/useChat.ts` uses `messageAPI` from `claude-code-leaked/web/lib/api/messages.ts`.

That path uses `parseStream()` from `claude-code-leaked/web/lib/api/stream.ts`, which expects “StreamEvent” JSON payloads.

In `claude-code-leaked/web/lib/api/types.ts`, `StreamEvent.type` includes:

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

## 4. Files / Exec (web shims)

The Claude web frontend also provides a large file-system shim layer that calls various endpoints.

Examples used by the browser FS shim (`claude-code-leaked/web/lib/platform/web/fs.ts`):

- `GET /api/fs/read?path=<path>&encoding=utf-8`
- `POST /api/fs/write`
- `GET /api/fs/list?path=<path>&withTypes=1`
- `POST /api/exec`
- `GET /api/fs/watch?path=<path>` (SSE watching)

Additionally, there are Next.js “proxy” endpoints under `claude-code-leaked/web/app/api/fs/*` and `claude-code-leaked/web/app/api/exec/*` that enforce sandboxing/security on the web side.

For an initial GemCode MVP, these can be implemented later; the required minimal endpoint set for chat streaming is `/api/chat` (+ whatever `/api/health` maps to).

## 5. Terminal WebSocket (`/ws`)

The Claude terminal UI uses a WebSocket at:

- WebSocket path: `/ws` (implemented in `claude-code-leaked/src/server/web/pty-server.ts`)

The backend performs:

- origin checks using `ALLOWED_ORIGINS` (optional)
- auth during `verifyClient` (implementation depends on `AUTH_PROVIDER`)
- rate limiting and session capacity checks

Connection query params include:

- `cols` and `rows` (terminal dimensions)
- optional `resume` token for reattaching to existing sessions

Message:

- on successful session creation, the server sends `{ type: "session", token: <token> }`

GemCode MVP scope decision:

- If you only need chat and file editing, you can defer `/ws`.
- If you want the in-browser terminal experience, you must implement `/ws` with the same session creation/resume semantics.

## 6. Permission and “auto-approve”

The Claude backend model/tool loop includes an `autoApprove` map for tool permission gating.

In the Claude backend’s legacy `/api/chat` handler (`claude-code-leaked/src/server/api/index.ts`), the default auto-approve passed to `streamMessage` is:

- `autoApprove: { file_read: true, file_write: false, bash: false }`

The UI may also require tool approval events for collaborative flows (see `tool_approval_needed` in the Claude backend SSE stream).

For GemCode:

- map your internal `GEMCODE_PERMISSION_MODE` / `--yes` to what the UI needs to allow.
- for an MVP, it is easiest to either:
  - auto-approve safe read-only operations and deny mutating ops until explicit confirmation, or
  - auto-approve everything in a local dev mode.

