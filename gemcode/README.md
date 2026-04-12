# GemCode — User manual

**GemCode** is a **local-first coding agent** that combines **Google Gemini** with the **[Agent Development Kit (ADK)](https://google.github.io/adk-docs/)** to work inside your repositories: it reads and edits files, runs allowlisted commands, searches code and the web, and can optionally use embeddings, deep research, browser automation, and live audio. Sessions persist under `.gemcode/`; permissions and audit logging keep operations explicit and inspectable.

This document is the **authoritative reference** for CLI behavior, configuration, tools, and REPL commands.

---

## Table of contents

1. [What GemCode is (detailed)](#what-gemcode-is-detailed)
2. [Architecture](#architecture)
3. [Requirements and install](#requirements-and-install)
4. [First run](#first-run)
5. [CLI commands](#cli-commands)
6. [Main CLI flags](#main-cli-flags)
7. [The `.gemcode/` directory](#the-gemcode-directory)
8. [Project context: `GEMINI.md`](#project-context-geminimd)
9. [Function tools (catalog)](#function-tools-catalog)
10. [REPL: slash commands](#repl-slash-commands)
11. [GemSkills](#gemskills)
12. [Curated memory](#curated-memory)
13. [Workspace trust](#workspace-trust)
14. [Output styles and rules](#output-styles-and-rules)
15. [Checkpoints, diff, and rewind](#checkpoints-diff-and-rewind)
16. [Multi-root workspaces (`/add-dir`)](#multi-root-workspaces-add-dir)
17. [Model routing and thinking](#model-routing-and-thinking)
18. [Capabilities](#capabilities)
19. [Permissions and interactive approval](#permissions-and-interactive-approval)
20. [Hooks](#hooks)
21. [Token budget, context, and compaction](#token-budget-context-and-compaction)
22. [MCP](#mcp)
23. [IDE bridge: `gemcode ide --stdio`](#ide-bridge-gemcode-ide-stdio)
24. [Eval harness and autotune](#eval-harness-and-autotune)
25. [Kaira scheduler](#kaira-scheduler)
26. [Live audio](#live-audio)
27. [Related components](#related-components)
28. [Environment variables](#environment-variables)
29. [Development and release](#development-and-release)

---

## What GemCode is (detailed)

**GemCode** is a single Python package that exposes a **`gemcode`** CLI. Every invocation is anchored to a **project root** (`-C`, default current directory). From there it:

- Builds a **GemCodeConfig** (model, capabilities, permissions, limits, paths).
- Optionally loads **MCP** toolsets from `.gemcode/mcp.json`.
- Constructs an ADK **Runner** with a root **LlmAgent** whose **system instruction** aggregates: global behavior, `GEMINI.md`, capability sections, tool manifest, optional **output style** and **rules**, the **skills manifest** (metadata only), and any **session-loaded GemSkills** (full bodies after `/gemskill`).
- Persists conversation state in **SQLite** (`.gemcode/sessions.sqlite`) keyed by **`--session`** / REPL session id.

### Modes of use

| Mode | How | Best for |
|------|-----|----------|
| **One-shot CLI** | `gemcode -C repo "prompt"` | Scripts, CI-style runs, quick questions. |
| **REPL** | `gemcode -C repo` (TTY, no prompt arg) | Long sessions, slash commands, exploration. |
| **TUI** | Same as REPL with `GEMCODE_TUI=1` (default when supported) | Scrollback, styled output, slash **completion menu**. |
| **IDE** | VS Code extension → `gemcode ide --stdio` | Editor-native Chat, diff apply, proposals. |
| **Kaira** | `gemcode kaira` | Background queue of independent agent jobs. |
| **Live audio** | `gemcode live-audio` | Voice → Gemini Live (separate from file-editing REPL). |

### “Memory” in GemCode (three different things)

| Mechanism | Where | Purpose |
|-----------|--------|---------|
| **Session history** | `sessions.sqlite` | Full turn-by-turn chat for a session id. |
| **Curated memory** | `GEMCODE_MEMORY.md`, `GEMCODE_USER.md` (under `.gemcode/`) | Small, **human-approved** facts injected when memory features are on; distinct from noisy auto-memory. |
| **Embedding memory** | `memories.jsonl` + ADK hooks (when enabled) | Retrieval-oriented storage when **`GEMCODE_ENABLE_MEMORY`** (and related) are on. |

Use **`/curated`** in the REPL to preview the curated snapshot; tools such as **`read_curated_memory`** / **`remember_fact`** operate on that layer.

### GemSkills at a glance (four ways)

| Mechanism | Effect |
|-----------|--------|
| **`/create gemskill <name> [description]`** | Creates **`.gemcode/skills/<name>/SKILL.md`** scaffold (new skill on disk). |
| **`/gemskill <name>`** | **Pins** the skill’s **full body** into the **system instruction** for the **current session** (until `/gemskill clear`, new session, or resume). Rebuilds the runner. |
| **`/append gemskill <name> <request>`** | One model turn instructed to **edit** that skill file according to `<request>`. |
| **`/skill <name>`**, **`/<name>`**, or tools **`load_skill`** | **One-shot** turn: inject expanded skill into the **user message** for that turn only (does not pin to system prompt). |

The built-in **`batch`** skill is manual-only (`disable_model_invocation`); use **`/batch <goal>`** to run it.

### Slash completion

The REPL and TUI use a **canonical** command list for Tab completion (`repl_commands.SLASH_COMMANDS`). Some spellings (e.g. `/quit`, `/logs`, `/embed`) still work in **`process_repl_slash`** but may not appear as separate menu rows—see descriptions in **`/help`**.

---

## Architecture

**Outer loop (CLI / REPL / IDE):** You choose project root (`-C`), session id, model mode, capabilities, and whether mutating tools are allowed (`--yes` or HITL). GemCode builds an ADK `Runner` with an `LlmAgent` and a **tool inventory** appropriate for that config.

**Inner loop (ADK):** The model is called repeatedly. Each turn it may emit **function calls**; ADK executes them and feeds results back until the run finishes, hits `max_llm_calls`, or stops for policy or circuit-breaker reasons.

**Multi-agent (optional):** ADK agent transfer can delegate to sub-agents (e.g. explorer/verifier patterns). Disable with `GEMCODE_ADK_AGENT_TRANSFER=0` if you need a single agent only.

**Subtasks:** `run_subtask` can spawn focused child runs for parallelized work (used heavily by the built-in **`/batch`** skill pattern).

---

## Requirements and install

- **Python** 3.11+
- **API key:** [Google AI Studio](https://aistudio.google.com/app/apikey) → `GOOGLE_API_KEY` (or `gemcode login` to store under `~/.gemcode/`)

```bash
cd gemcode
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Copy **`.env.example`** to `.env` in your project (or globally) and set at least `GOOGLE_API_KEY`.

---

## First run

1. **Workspace trust** — On first use in a directory, GemCode may ask you to **trust** the folder so file and shell tools are allowed. Trust is recorded so you are not prompted every time.
2. **API key** — If `GOOGLE_API_KEY` is unset in a TTY session, GemCode can prompt once and save the key (same store as `gemcode login`).
3. **`.gemcode/`** — Created under the project root for sessions, audit log, policy, tool results, etc.

Non-interactive environments (CI, pipes) must set `GOOGLE_API_KEY` explicitly and may need to pre-trust or set env vars that skip prompts (see `.env.example`).

---

## CLI commands

| Invocation | Purpose |
|------------|---------|
| `gemcode [prompt]` | Run one user message and print the model’s final text. Prompt omitted + TTY → **REPL**. Prompt omitted + stdin → read prompt from stdin. |
| `gemcode -C DIR …` | Use **DIR** as `project_root` (recommended instead of running from `~`). |
| `gemcode login` | Save or update the Google API key in the user credential store. |
| `gemcode models` (aliases: `list-models`, `list_models`) | List Gemini models (`--show-all` for full list). |
| `gemcode tools list` \| `gemcode tools smoke` | Inspect or validate tool declarations for a project (optional `--deep-research`, `--maps-grounding`, `--embeddings`, `--memory`). |
| `gemcode eval` | Run automated gates (tool smoke, `pytest` if `tests/` exists; optional `--llm` golden prompts). Writes **`.gemcode/evals/last_eval.json`**. Exit non-zero on failure. |
| `gemcode autotune init --tag NAME` | Create/checkout an `autotune/<NAME>` branch (scaffolding for iterative tuning). |
| `gemcode autotune eval` | Run eval suite and append to **`.gemcode/evals/autotune_ledger.jsonl`**. |
| `gemcode live-audio` | Microphone → Gemini Live API (see [Live audio](#live-audio)). |
| `gemcode kaira` | Stdin-line → queued jobs scheduler (see [Kaira](#kaira-scheduler)). |
| `gemcode ide --stdio` | **JSONL IDE protocol** on stdin/stdout for editor extensions (hidden entry; used by VS Code). |

**Files with a prompt (multimodal):** attach one or more files Gemini can read (images, **PDF**, audio, video, plain text, etc.) for that message only:

```bash
gemcode -C . --attach ./screenshot.png "Why does this UI look wrong?"
gemcode -C . --attach ./report.pdf "What are the main conclusions?"
gemcode -C . --image a.png --image b.png "Compare these two layouts"
```

(`--image` is an alias of `--attach`.)

In the **REPL / TUI**, queue files for the **next** message: `/attach path/to.pdf` (repeat for multiple), then type your question. Use `/attach list`, `/attach clear`. Aliases: `/image`, `/img`, `/file`.

---

## Main CLI flags

| Flag | Meaning |
|------|---------|
| `-C`, `--directory` | Project root (default: current working directory). |
| `--session` | Session id for SQLite-backed history (reuse to continue a conversation). |
| `--yes` | Allow mutating tools (`write_file`, `search_replace`, …). Shell still restricted by allowlist + permission mode. |
| `--interactive-ask` | **HITL:** prompt in the terminal to approve each mutating tool instead of requiring `--yes` up front. |
| `--model` | Override default Gemini model id. |
| `--model-mode` | `auto\|fast\|balanced\|quality` — routing strategy (see `.env.example`). |
| `--deep-research` | Enable Gemini built-in search/url tools and deep-research routing. |
| `--maps-grounding` | Opt-in Maps grounding inside deep research (can interact badly with other built-ins on some models). |
| `--embeddings` | Enable semantic file search (`semantic_search_files`) and related plumbing. |
| `--capability-mode` | `auto\|research\|embeddings\|computer\|audio\|all` — force or hint capability bundles. |
| `--tool-combination-mode` | Gemini 3 **tool context circulation**: `deep_research\|always\|never\|auto`. |
| `--mcp` | Load MCP toolsets from `.gemcode/mcp.json` (requires `pip install -e ".[mcp]"`). |
| `--max-llm-calls` | Cap model↔tool iterations (`RunConfig.max_llm_calls`). |
| `--attach PATH` | Attach file(s) for **this** CLI message only (repeat flag). Gemini-supported MIME (e.g. images, PDF, audio, video). Default max ~20 MiB each (`GEMCODE_MAX_ATTACHMENT_BYTES`). Alias: `--image`. |

Kaira and `live-audio` accept overlapping options (project root, `--yes`, research/embeddings, etc.); run `gemcode kaira -h` / `gemcode live-audio -h` for full lists.

---

## The `.gemcode/` directory

State is **project-local** (unless noted).

| Path / artifact | Purpose |
|-----------------|---------|
| `sessions.sqlite` | ADK session service: conversation history for `--session` ids. |
| `GEMCODE_MEMORY.md`, `GEMCODE_USER.md` | **Curated memory** (see [Curated memory](#curated-memory)). |
| `audit.log` | JSONL audit: tool usage, model usage, terminal reasons, optional tool-use summaries. |
| `tool-results/` | Offloaded large tool outputs; references like `tool_result:<sha256>`. |
| `artifacts/` | File artifacts (ADK `FileArtifactService`). |
| `policy.json` | Self-tuning profile for dynamic token / evidence budgets. |
| `memories.jsonl` | Embedding-backed memory when `GEMCODE_ENABLE_MEMORY=1`. |
| `notes.md` | Agent notes surfaced via `/notes`. |
| `evals/last_eval.json` | Latest `gemcode eval` record. |
| `evals/autotune_ledger.jsonl` | Rows from `gemcode autotune eval`. |
| `skills/<name>/SKILL.md` | **GemSkills** (project-scoped; see [GemSkills](#gemskills)). |
| `output-styles/<name>.md` | **Output style** prompts (see [Output styles](#output-styles-and-rules)). |
| `rules/*.md` | **Rule** files with optional path gating (see [Rules](#output-styles-and-rules)). |
| `hooks/post_turn` | Optional executable **post-turn** hook (or `GEMCODE_POST_TURN_HOOK`). |
| `hooks/pre_tool_use`, `post_tool_use`, `session_start`, `session_stop` | Optional lifecycle hooks (executable scripts; JSON on stdin). |
| `settings.json` | Optional **permission rules** (`allow` / `deny` patterns for bash, etc.). |
| `mcp.json` | MCP server definitions when using `--mcp`. |
| Checkpoints | Managed by `gemcode.checkpoints` — listed and restored via `/rewind` and tools. |

User-wide overrides can live under `~/.gemcode/` (credentials, global `settings.json`, personal skills/styles/rules).

---

## Project context: `GEMINI.md`

If present at the **repository root**, `GEMINI.md` is injected into the system instruction so the model consistently sees your project goals, conventions, and boundaries.

Use **`/init`** (or `/init force`) in the REPL to scaffold or refresh `GEMINI.md` from the current tree.

---

## Function tools (catalog)

Tools are registered in `gemcode/tools/` and exposed to the model as ADK function tools. Exact availability may depend on config (e.g. memory, MCP, computer use).

### Planning and meta

| Tool | Role |
|------|------|
| `todo_write` / `todo_read` | Task lists for multi-step work. |
| `think` | Explicit reasoning step (useful for hard problems). |

### Filesystem and search

| Tool | Role |
|------|------|
| `read_file`, `list_directory`, `glob_files` | Read-only navigation. |
| `grep_content` | Ripgrep-style search (respects allowed roots). |
| `repo_map` | Compact symbol-oriented map of the repo. |
| `write_file`, `search_replace` | Mutations (require `--yes` or HITL approval in default mode). |
| `move_file`, `delete_file` | File tree mutations (same permission rules). |

### Shell and processes

| Tool | Role |
|------|------|
| `run_command` | Allowlisted one-shot commands. |
| `bash` | Pipelines and redirects; supports `background=True`. |
| `list_tasks`, `task_output`, `kill_task` | Background task control for `bash(..., background=True)`. |

### Notebooks

| Tool | Role |
|------|------|
| `notebook_read`, `notebook_edit` | Jupyter notebook cells. |

### Web

| Tool | Role |
|------|------|
| `web_search`, `web_fetch` | Programmatic web search and fetch (policy applies). |

### Tool results and recovery

| Tool | Role |
|------|------|
| `load_tool_result` | Load a prior offloaded blob by `tool_result:<sha>`. |
| `checkpoints_list`, `checkpoint_undo` | List/restore **checkpoints** after mutating operations. |

### Memory and skills

| Tool | Role |
|------|------|
| `remember_fact`, `read_curated_memory` | Curated, safe-to-inject persistent notes (separate from full session memory). |
| `list_skills`, `load_skill`, `skills_manifest` | Discover and load **GemSkills** on demand. |
| `load_memory` | (When memory enabled) ADK on-demand memory search. |

### Parallelism

| Tool | Role |
|------|------|
| `run_subtask` | Spawn a focused sub-run for isolated tasks (used by `/batch` patterns). |

### Optional bundles (not in the minimal core list above)

- **Deep research:** `google_search`, `url_context`, optional `google_maps_grounding`.
- **Embeddings:** `semantic_search_files`; with memory, embedding-backed storage.
- **Computer use:** ADK `ComputerUseToolset` + Playwright (separate install and flags).
- **MCP:** Tools loaded from configured servers.

**Vendor file policy:** Writes to certain third-party instruction filenames (`CLAUDE.md`, `AGENTS.md`, `*.local` variants, `.cursorrules`, …) are blocked; use `GEMINI.md` and `.gemcode/notes.md` instead. The agent instruction always states this; `write_file` / `search_replace` enforce it.

---

## REPL: slash commands

In interactive mode, lines starting with `/` are **slash commands** (see `repl_commands.py` and `tui/input_handler.py` for the full set). Below is a grouped reference.

### Project and context

| Command | Purpose |
|---------|---------|
| `/attach <path>`, `/attach list`, `/attach clear` | Queue file(s) for the **next** message (Gemini multimodal). Aliases: **`/image`**, **`/img`**, **`/file`**. |
| `/trust`, `/trust on`, `/trust off` | Show, grant, or revoke **workspace trust** for the project root (stored in `~/.gemcode/trust.json`; required for file/shell/git tools). |
| `/init` \| `/init force` | Analyze the repo and generate or overwrite `GEMINI.md`. |
| `/cost` | Token usage and estimated cost for the session. |
| `/notes`, `/notes clear`, `/notes edit` | View, clear, or edit `.gemcode/notes.md`. |
| `/create gemskill <name> [description]` | **Create** a new GemSkill (scaffold `SKILL.md`). |
| `/gemskill <name>` | **Load** an existing skill into the **session system prompt** (until `/gemskill clear` or new session). |
| `/gemskill list` \| `/gemskill clear` | List skills or unload all session-loaded skills. |
| `/append gemskill <name> <request>` | **Iterate** the skill file: model edits `SKILL.md` per your request. |
| `/curated` | Show **curated memory** snapshot (`.gemcode/GEMCODE_MEMORY.md`, `GEMCODE_USER.md`). |
| `/style`, `/style <name>\|off` | List or activate **output styles** (`.gemcode/output-styles/*.md`). |
| `/rules` | Show **rule** files from `.gemcode/rules/` (with path gating). |
| `/diff`, `/diff last`, `/diff cp_…` | Git diff, or **checkpoint → workspace** diff. |
| `/rewind` \| `/checkpoint` | List or restore **checkpoints**. |
| `/add-dir`, `/add-dir list`, `/add-dir remove <name>` | Add extra read/search roots (**multi-root**). |
| `/batch <goal>` | Run the built-in **batch** orchestration skill (large parallel changes). |

### Session and diagnostics

| Command | Purpose |
|---------|---------|
| `/help` | Short help. |
| `/status` | Model, capabilities, thinking, limits, risk/context telemetry; **`loaded_skills`** (session-pinned GemSkills from `/gemskill`). |
| `/config` | Dump active config fields. |
| `/session`, `/session list`, `/session name`, `/session resume`, `/session new` | Session management; `/clear` aliases `/session new`. |
| `/compact`, `/compact <focus>` | Force context compaction / summarization. |
| `/review`, `/review <path>` | Parallel code review pass. |
| `/context` | Context pressure and token breakdown (includes styles, rules, skills manifest, touched paths). |
| `/audit [N]` | Tail of `audit.log`. |
| `/tools` | Tool inventory for current config. |
| `/tools smoke` | Declaration compile check only (lists failures). |
| `/eval`, `/eval llm` | **Eval harness**: tool smoke + `pytest` if a `tests/` tree exists; optional LLM golden prompts (costs tokens). Writes `.gemcode/evals/last_eval.json`. |
| `/autotune init <tag>` | Create git branch `autotune/<tag>` (requires a git repo). |
| `/autotune eval`, `… llm` | Run eval and append `.gemcode/evals/autotune_ledger.jsonl`. |
| `/login` | How to run **`gemcode login`** (API key is set outside the REPL). |
| `/live-audio` | How to run **`gemcode live-audio`** (mic → Gemini Live). |
| `/doctor` | Environment sanity check. |
| `/version` | Version string. |
| `/exit` | Leave the REPL. |

### Model

| Command | Purpose |
|---------|---------|
| `/model`, `/model use <id>`, `/model list` | Show or set model; list Gemini ids. |
| `/mode`, `/mode <fast\|balanced\|quality\|auto>` | Model mode strategy. |

### Capabilities

| Command | Purpose |
|---------|---------|
| `/computer`, `/computer on\|off`, `/computer url` | Browser automation (Playwright). |
| `/research`, `/research on\|off` | Deep research tools. |
| `/maps`, `/maps on\|off` | Maps **grounding** toggle (runner rebuild). |
| `/embeddings on\|off` | Semantic search tool. |
| `/caps`, `/caps …` | View or bulk-toggle capabilities. |
| `/memory`, `/memory on\|off` | Persistent memory. |

### Thinking and limits

| Command | Purpose |
|---------|---------|
| `/thinking …` | Verbose/brief/off, budget/level for Gemini 2.5/3.x. |
| `/limits`, `/limits calls <N>` | Execution limits. |
| `/budget`, `/budget <N>\|off` | Per-turn token budget. |

### Other

| Command | Purpose |
|---------|---------|
| `/permissions` | Permission mode and HITL settings. |
| `/hooks` | Post-turn and lifecycle hook paths. |
| `/kaira` | How to run the **Kaira** daemon. |
| `/code on\|off` | Sandboxed Python executor (ADK `BuiltInCodeExecutor`). |
| `/plan on\|off` | Plan-before-act mode. |

The TUI (when `GEMCODE_TUI=1` and terminal supports it) provides **slash completion** and a scrollback-style UI.

---

## GemSkills

**GemSkills** are reusable markdown playbooks with optional YAML frontmatter.

- **Locations:** `.gemcode/skills/<name>/SKILL.md` (project), `~/.gemcode/skills/<name>/SKILL.md` (user).
- **Discovery:** Only **metadata** (name + description) is preloaded into context for token efficiency. Full body loads **on demand** via `/skill <name>`, `/<name>`, or tools `load_skill` / `list_skills`.
- **Built-in:** **`batch`** — parallel large-change workflow (map → units → `run_subtask` → verify). Exposed as `/batch <goal>`; not auto-invoked by the model (`disable_model_invocation`).

- **`/create gemskill <name>`** — create a new skill directory.
- **`/gemskill <name>`** — pin the full skill body into the system prompt for this session.
- **`/append gemskill <name> <text>`** — one-shot turn for the model to revise that skill on disk.

---

## Curated memory

**Curated memory** is a small, **intentional** text layer separate from raw session logs or embedding retrieval:

- **Project facts:** `.gemcode/GEMCODE_MEMORY.md` (conventions, commands, architecture notes the team wants the agent to see).
- **User preferences (per project):** `.gemcode/GEMCODE_USER.md`.

Legacy filenames **`.gemcode/MEMORY.md`** and **`.gemcode/USER.md`** are still read if the new names are absent.

Content is **sanitized** before append (length and sensitivity heuristics). The REPL command **`/curated`** prints a bounded snapshot of what would be injected. When the **memory** capability is enabled, curated material can be combined with broader memory tools—see **`remember_fact`**, **`read_curated_memory`** in the [function tools](#function-tools-catalog) table.

---

## Workspace trust

File, shell, and related tools require the **project root** to be **trusted**. Trust is stored in **`~/.gemcode/trust.json`** (path configurable via **`GEMCODE_HOME`**), not inside the repo.

- **`/trust`** — show whether the current root is trusted.
- **`/trust on`** / **`/trust off`** — grant or revoke trust for **`cfg.project_root`**.

Without trust, mutating and shell tools remain blocked even if **`--yes`** is set—this reduces accidental execution when the cwd is wrong (e.g. home directory).

---

## Output styles and rules

### Output styles

- **Paths:** `.gemcode/output-styles/<name>.md` or `~/.gemcode/output-styles/<name>.md` (project wins on name clash).
- **Names:** lowercase letters, digits, hyphens (e.g. `concise-bullets.md` → `concise-bullets`).
- **REPL:** `/style` lists styles; `/style <name>` applies for the session; `/style off` clears.
- **Env:** `GEMCODE_OUTPUT_STYLE` or session config `cfg.output_style`.

### Rules

- **Paths:** `.gemcode/rules/*.md` and `~/.gemcode/rules/*.md`.
- **Frontmatter:** Optional `paths:` list — rules can apply only when touched paths match (gating).
- **REPL:** `/rules` shows what is loaded.

---

## Checkpoints, diff, and rewind

Mutating tools participate in a **checkpoint** system so you can compare and roll back.

- **Tools:** `checkpoints_list`, `checkpoint_undo`.
- **REPL:** `/diff` shows `git diff` when inside a git repo; outside git or when you need history, `/diff last` or `/diff cp_<id>` compares **checkpoint vs workspace**.
- **REPL:** `/rewind` (alias `/checkpoint`) lists or restores checkpoints.

---

## Multi-root workspaces (`/add-dir`)

Some projects span multiple directories. **`/add-dir <path>`** registers additional roots for **read/search** operations with path safety checks (`resolve_under_allowed_roots`). Use `/add-dir list` and `/add-dir remove <name>` to manage them.

---

## Model routing and thinking

- **`GEMCODE_MODEL`**, **`GEMCODE_MODEL_MODE`** (`fast`, `balanced`, `quality`, `auto`), and family toggles **`GEMCODE_MODEL_FAMILY_MODE`** (`primary`, `alt`, `auto`) select among Gemini 3.x vs 2.5 defaults.
- **Thinking:** For Gemini 3.x, `GEMCODE_THINKING_LEVEL` and related env vars; for 2.5, thinking budget. `/thinking` in the REPL adjusts behavior; `GEMCODE_DISABLE_THINKING`, `GEMCODE_INCLUDE_THOUGHT_SUMMARIES` affect cost and verbosity.

See `.env.example` for the full matrix.

---

## Capabilities

| Capability | Enable | Notes |
|------------|--------|--------|
| Deep research | `--deep-research`, `GEMCODE_ENABLE_DEEP_RESEARCH` | `google_search`, `url_context`; optional Maps. |
| Embeddings | `--embeddings`, `GEMCODE_ENABLE_EMBEDDINGS` | `semantic_search_files`. |
| Memory | `GEMCODE_ENABLE_MEMORY` | File-backed memories + optional `load_memory`. |
| Computer use | `GEMCODE_ENABLE_COMPUTER_USE` / `--capability-mode computer` | Playwright; requires install + permissions. |
| Audio (live) | `gemcode live-audio` | Separate streaming path. |

**Capability routing** (`--capability-mode` or `GEMCODE_CAPABILITY_MODE`) can bundle tools and route to role-specific models when configured.

---

## Permissions and interactive approval

- **`GEMCODE_PERMISSION_MODE`:** `default` vs `strict` (strict denies writes and shell unless explicitly allowlisted).
- **`--yes`:** Treat as approval for mutating tools for that process.
- **`--interactive-ask` / `GEMCODE_INTERACTIVE_PERMISSION_ASK`:** Prompt **in-run** for each sensitive tool call if `--yes` was not passed.
- **Settings:** `.gemcode/settings.json` and `~/.gemcode/settings.json` can **allow** / **deny** patterns (see `/permissions` output).
- Policy denials **do not** increment the consecutive-failure circuit breaker.

---

## Hooks

- **Post-turn:** `GEMCODE_POST_TURN_HOOK` or executable `.gemcode/hooks/post_turn`.
- **Lifecycle:** Optional executable scripts `pre_tool_use`, `post_tool_use`, `session_start`, `session_stop` under `.gemcode/hooks/` (with optional `.sh`/`.py` suffix). They receive **JSON on stdin**; `pre_tool_use` can deny a tool with non-zero exit.

---

## Token budget, context, and compaction

GemCode uses **dynamic token policy** (context pressure + task risk), **tool result offloading**, optional **ADK event compaction**, optional **MVP compaction** of `Content` items, and session ceilings (`GEMCODE_MAX_SESSION_TOKENS`, `GEMCODE_TOKEN_BUDGET`). Key toggles:

- `GEMCODE_DYNAMIC_TOKEN_POLICY`, `GEMCODE_DYNAMIC_RISK_POLICY`, `GEMCODE_TOOL_RESULT_OFFLOAD`
- `GEMCODE_AUTOCOMPACT`, `GEMCODE_ADK_EVENTS_COMPACTION`, etc.

`/status` and `/context` surface live telemetry (`risk_score`, `context_percent_left`, …).

---

## MCP

1. Install: `pip install -e ".[mcp]"`.
2. Configure **`.gemcode/mcp.json`** (see `mcp_loader.py`).
3. Run with **`--mcp`** so configured toolsets attach to the runner.

---

## IDE bridge: `gemcode ide --stdio`

Editor extensions (including the official VS Code extension) spawn:

```bash
gemcode ide --stdio
```

and speak a **JSONL protocol** (`ide_protocol.py`, `ide_stdio.py`) for chat, tool proposals, diffs, apply/undo, and checkpoints — without the extension writing files unless the user confirms.

---

## Eval harness and autotune

| Command | Purpose |
|---------|---------|
| `gemcode eval [-C DIR] [--llm] [--model M]` | Run gates; write `.gemcode/evals/last_eval.json`; exit `1` on failure. |
| `gemcode autotune init --tag NAME [-C DIR]` | Git branch scaffolding for experiments. |
| `gemcode autotune eval …` | Eval + append **ledger** JSONL with git metadata. |

Use **`--llm`** sparingly; it runs golden prompts and costs tokens.

---

## Kaira scheduler

```bash
gemcode kaira [-C DIR] [--concurrency N] [--yes] [--interactive-ask] …
```

Reads **one prompt per line** from stdin; jobs run on a priority queue. The model may enqueue more work via Kaira tools (`kaira_enqueue_prompt`, `kaira_sleep_ms`). See `/kaira` in the REPL for a short usage summary.

---

## Live audio

```bash
gemcode live-audio [-C DIR] [--seconds N] [--rate HZ] [--language CODE] [--model M] …
```

Streams microphone audio to the **Gemini Live** API. Requires optional dependencies (`sounddevice`, `numpy`). Intended for voice-driven sessions, not file editing by default.

---

## Related components

| Component | Role |
|-----------|------|
| **VS Code extension** (`gemcode-vscode/`) | Launch CLI, sidebar control center, Chat + diff apply, `gemcode ide --stdio`. |
| **Web API** (`gemcode-web-api/`) | Example Node server: terminals, env wiring; can integrate with SSE chat adapters. |
| **Web UI contract** (`docs/web-ui-contract.md`) | Documented **SSE / `POST /api/chat`** expectations for compatible frontends. |

---

## Environment variables

The canonical commented list is **`gemcode/.env.example`**. It covers:

- Models, modes, and family routing  
- Capabilities (research, embeddings, computer, audio)  
- Permissions, HITL, allowlists  
- Context windows, compaction, autocompact, ADK compaction  
- Tool output limits, audit, hooks, MCP, verbose errors  

Set variables in the shell, in a `.env` file at the project root, or in CI secrets — **never commit API keys**.

---

## Development and release

```bash
pip install -e ".[dev]"
pytest
```

### Release workflow (tags → PyPI)

The repo can publish to PyPI on **`v*`** tags (see `.github/workflows/publish-pypi.yml`).

```bash
# Bump version in gemcode/pyproject.toml, then:
git add -A
git commit -m "release: vX.Y.Z"
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin HEAD
git push origin vX.Y.Z
```

---

## References

- [Google ADK documentation](https://google.github.io/adk-docs/)
- [Google AI Studio](https://aistudio.google.com/) — API keys
- Do not commit proprietary leaked trees into this package; keep reference clones private if used.
