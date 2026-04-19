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

## Kaira operations
Kaira is a queue-based daemon.

Operational expectations:
- reads prompts from stdin
- schedules jobs with concurrency
- not a TUI
- best used for background or repeated work

Recommended operator guidance:
- use explicit `-C`
- use explicit `--session`
- choose `--yes` or `--interactive-ask` intentionally
- for non-interactive jobs (no tool-confirmation IPC, autonomous `get_user_choice`), use `--super` or `GEMCODE_SUPER_MODE=1` (see [`tools-and-permissions.md`](tools-and-permissions.md#super-mode-fully-autonomous))

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
