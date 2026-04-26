# Operations, Troubleshooting, and Release

## Operational state
GemCode is stateful. Production operators should understand where state accumulates:
- sessions
- notes
- memory
- logs
- tool-result offload
- artifacts
- checkpoints
- evaluation records

Primary location:
- `.gemcode/`

## Audit and inspection

### Audit log
File:
- `.gemcode/audit.log`

Use:
- `/audit`
- `/status`
- `/context`

### Debug logging
When enabled, GemCode can emit:
- `.gemcode/debug.yaml`

Relevant code:
- `gemcode/src/gemcode/session_runtime.py`

## Common runtime issues

### Wrong project root
Symptoms:
- trust issues
- wrong files visible
- `.gemcode/` state created in the wrong directory

Fix:
- use `-C`
- verify current working directory

### Invalid model id
Symptoms:
- “Model not found”
- failures when deep research or computer-use routing is enabled

Fix:
- verify configured model ids
- verify installed version vs repo version

### AFC warnings
Symptoms:
- warnings about tools not being compatible with automatic function calling

Fix:
- choose all tools or callable-only tools when prompted
- understand that MCP/OpenAPI/toolsets may affect AFC

### Cache cleanup errors
Symptoms:
- log line like `Failed to cleanup cache cachedContents/...` with `403 PERMISSION_DENIED` and
  `CachedContent not found (or permission denied)`

Cause:
- ADK’s Gemini **context cache** (see `GEMCODE_CONTEXT_CACHE` in [`configuration.md`](configuration.md#context-and-budgets)) reuses a server-side cache. When the entry
  has **already expired** (TTL) or been evicted, a follow-up **delete** can return 403
  “not found” even though nothing is wrong for the user.

Impact:
- usually **non-fatal** noise; GemCode treats that class of delete failure as a no-op and
  logs it at **debug** only.

If you still want to avoid context caching entirely (saves this class of message and
  uses a simpler request path, at higher input-token cost on long sessions), set
`GEMCODE_CONTEXT_CACHE=0`.

## Kaira daemon operations (GemCode Runtime)
Kaira is the name of GemCode’s runtime daemon. It is a queue-based service that runs **GemCode jobs** in the background. It is not an external add-on; it uses the same GemCode tool surface and configuration.

Operational expectations:
- reads prompts from stdin
- schedules jobs with concurrency
- not a TUI
- best used for background or repeated work

### Multi-terminal attach (same project)
The Kaira daemon exposes a local Unix-socket control plane and event stream at:
- `.gemcode/ipc.sock`

Multiple GemCode REPL/TUI instances can attach to the same daemon at the same time.

Behavior:
- when the scrollback TUI is running, it auto-connects (by default) and streams runtime job output inline
- the TUI also handles runtime HITL permission prompts via IPC so background jobs can request approvals

Relevant settings:
- `GEMCODE_KAIRA_AUTO_CONNECT=1` (default): TUI auto-connects when `.gemcode/ipc.sock` exists
- `GEMCODE_KAIRA_SOCKET=/path/to/ipc.sock`: override socket path

### Runtime alias
`gemcode runtime` is the preferred spelling. `gemcode kaira` is an alias kept for compatibility.

Operational note:
- If you start the runtime from inside an agent workspace (`.gemcode/agents/...`), it resolves back to the shared “fleet root” (parent project containing `.gemcode/org.json`) so it still has access to the full feature surface: agent registry, MCP config, automations, and job storage.

Recommended operator guidance:
- use explicit `-C`
- use explicit `--session`
- choose `--yes` or `--interactive-ask` intentionally
- for non-interactive jobs (no tool-confirmation IPC, autonomous `get_user_choice`), use `--super` or `GEMCODE_SUPER_MODE=1` (see [`tools-and-permissions.md`](tools-and-permissions.md#super-mode-fully-autonomous))

### Runtime bus (client-to-client messages)
In addition to job events, the IPC stream also supports a lightweight message bus:
- event: `bus_message`
- filters: subscribe can optionally filter bus messages by `topics` and `to` address

This enables multi-client coordination (e.g. two terminals running GemCode) without requiring a second transport.

Practical usage:
- `/agent assign <member> <task...>` publishes `topic=org.assign` to the runtime (if available)
- runtime reacts by enqueueing a background run that performs the delegation and emits `topic=org.report`

## Eval and autotune

### Eval
Command:
```bash
gemcode eval -C .
```

Artifacts:
- `.gemcode/evals/last_eval.json`

### Autotune
Commands:
```bash
gemcode autotune init --tag name -C .
gemcode autotune eval -C .
```

Artifacts:
- `.gemcode/evals/autotune_ledger.jsonl`

## Release workflow

### Package version
Python package version lives in:
- `gemcode/pyproject.toml`

### Tag-driven publishing
PyPI publishing is driven by `v*` tags.

Safe release flow:
1. bump package version
2. commit version bump
3. tag the release
4. push branch
5. push tag

Example:

```bash
git add gemcode/pyproject.toml
git commit -m "chore(release): bump gemcode to X.Y.Z"
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main
git push origin vX.Y.Z
```

### Common release failure
If PyPI rejects an upload with “file already exists”, the package version was not bumped even though a new tag was pushed.

Fix:
- bump `gemcode/pyproject.toml`
- commit
- create a new tag
- push again

## Documentation maintenance guidance
When shipping new features, update:
- root README for overview changes
- docs index
- the relevant subsystem page
- release/operations docs if new state, env vars, or deployment behavior is introduced
