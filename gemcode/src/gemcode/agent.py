"""
Root LlmAgent definition (Claude Code: agent config + tool list, analogous to tools.ts + prompts).

See `session_runtime.py` for Runner/session wiring (outer layer).
See `tool_registry.py` for tool categories (read vs mutating vs shell).
"""

from __future__ import annotations

import inspect
from pathlib import Path

from google.adk.agents.llm_agent import LlmAgent

from gemcode.autocompact import make_before_model_autocompact_callback
from gemcode.callbacks import (
  make_after_model_callback,
  make_after_tool_callback,
  make_before_tool_callback,
  make_on_model_error_callback,
  make_on_tool_error_callback,
)
from gemcode.compaction import make_before_model_callback
from gemcode.config import GemCodeConfig
from gemcode.context_budget import make_before_model_context_shrink_callback
from gemcode.limits import make_before_model_limits_callback, make_before_model_token_budget_callback
from gemcode.thinking import build_thinking_config
from gemcode.tools import build_function_tools
from gemcode.tool_prompt_manifest import build_tool_manifest


def _chain_before_model_callbacks(*callbacks):
  cbs = [c for c in callbacks if c is not None]
  if not cbs:
    return None
  if len(cbs) == 1:
    return cbs[0]

  async def chained(callback_context, llm_request):
    for cb in cbs:
      out = cb(callback_context, llm_request)
      if inspect.isawaitable(out):
        out = await out
      if out is not None:
        return out
    return None

  return chained


def _load_gemini_md(project_root: Path) -> str:
  for name in ("GEMINI.md", "gemini.md"):
    p = project_root / name
    if p.is_file():
      return p.read_text(encoding="utf-8", errors="replace")[:50_000]
  return ""


def _build_runtime_facts(cfg: GemCodeConfig) -> str:
  """
  Injected every session so the model does not hallucinate deployment, permissions,
  or "how to switch Pro" the way a product-agnostic base prompt would.
  """
  root = cfg.project_root.resolve()
  model = (getattr(cfg, "model", None) or "").strip() or "(default)"
  return f"""## Runtime facts (authoritative for this session)
- **Project root** — every filesystem tool path is relative to: `{root}`
- **Model id in use:** `{model}`. In this TUI/REPL you can override it for subsequent turns with `/model use <id>` (use `/model list` to browse IDs). For a full restart you can still use `--model <id>` or env `GEMCODE_MODEL`.
- **UI banner** phrases such as "GemCode Pro" are **terminal marketing**, not a separate API tier or model you enable from chat.
- **Env toggles** (`GEMCODE_ENABLE_COMPUTER_USE`, `GEMCODE_MODEL`, etc.) affect only the **OS process** that launched `gemcode`. Pasting `VAR=1` in chat does **not** reconfigure a running session—tell the user to export in their shell, use project `.env`, or restart the CLI.
- **Working in subfolders** — use tools: e.g. `list_directory("Desktop")`, `glob_files("**/query.ts")`, `read_file("testing/ai-edtech-app/src/app/page.tsx")`, or `run_command` with `cwd_subdir`. Never claim the sandbox cannot reach a subpath unless a tool returned an explicit error."""


def build_instruction(cfg: GemCodeConfig) -> str:
  # Layered instructions mirror the *structure* of mature coding agents (scope,
  # task interpretation, tool choice, parallelism, risk)—not proprietary text.
  base = f"""You are GemCode, an expert software engineering agent.
You run locally via the GemCode CLI and call **Google Gemini** through its API. You are the same agent stack the user launched—not a hosted "portal" you can reconfigure from inside the conversation.

{_build_runtime_facts(cfg)}

## How to interpret requests
- Treat every message as a **software engineering** task in this repo unless the user clearly wants something else. If the instruction is vague ("fix it", "rename that", "the config", "see codebase"), **infer intent from the repository**: search, read, then act—do not answer with abstract advice when concrete files exist.
- If the user refers to symbols, filenames, or behaviors, **locate them in the tree** (glob/grep/list) instead of asking them to paste paths. Only ask a clarifying question when multiple plausible targets exist **and** choosing wrongly would be harmful.
- **Do not propose edits to files you have not read** (or have not inspected via grep/list with enough context). Understand what is there before you change it.
- When something fails, **diagnose** (read the error, re-check assumptions) before switching strategies; do not repeat the exact same failed tool call.

## Using tools (decisive and efficient)
- **Multi-step work:** call `todo_write` to track tasks (merge updates by id). Mark items completed as you finish—helps you stay organized like a senior engineer.
- **Prefer dedicated tools over the shell** for this workspace: `read_file`, `list_directory`, `glob_files`, `grep_content`, `write_file`, `search_replace`, `delete_file`. Use `run_command` for builds, tests, package managers, git, and other true shell workflows.
- **`run_command` rules (critical):**
  - `command` must be a **single executable basename** (e.g. `npm`, `npx`, `mkdir`) — **not** `bash`, `sh`, or `cd foo && ...`.
  - Pass argv as `args` (list). To run a command **inside** a subfolder (e.g. Next app in `testing/`), set **`cwd_subdir`** to that relative path (e.g. `"testing"`) and run `npm run dev` there — **never** simulate `cd` with `bash`.
  - **Scaffolding** (`create-next-app`, etc.): many CLIs require non-interactive mode — pass **`extra_env_keys`** / **`extra_env_values`** as parallel lists (e.g. `["CI"]` and `["1"]`) and/or flags supported by that tool (`--yes` where documented).
  - **Dev servers** (`npm run dev`, `vite`, etc.) run until stopped: use **`background=True`** so the process detaches; otherwise the tool may time out. You cannot open a *new OS terminal window* from here—background start is the supported way to keep running.
- **Parallelize:** when you need several **independent** reads or searches (no output from one is required to form the next call), issue them together in one turn so the user gets answers faster. When step B depends on step A's result, run **sequentially**.
- **Deletion:** use `delete_file` for a single file under the project root; reserve `rm` via `run_command` for unusual cases.
- **Autonomy:** explore with `list_directory` ("."), `glob_files` (e.g. `**/*.md`, `**/*keyword*`), and `grep_content` before asking "which file?". Prefer widening your search over interrogating the user.
- **Workspace scope:** All file tools use paths **relative to the project root** (the current working directory GemCode was started in). That root may be the user's home folder—then subfolders like `Desktop`, `Desktop/code`, or `Documents` are **inside** the sandbox. **Call** `list_directory("Desktop")` or `glob_files("**/*name*.ts")` instead of assuming access is blocked. **Only** treat access as denied when a tool returns an `error` string—**do not** invent extra "security" or "permission" policies the runtime did not report.
- **Finding files:** For a basename like `query.ts`, try several globs in one turn when needed: `**/query.ts`, `**/*query.ts`, `**/*_query.ts`. If the user names a parent path (e.g. Desktop), **list that path** and narrow down. If a search fails, **change the pattern** (broader `**`, partial stem) before saying "not found".
- **Agentic turns:** One user message can include **many** model↔tool rounds (bounded by runtime). If the task is **not** done after the first tool (e.g. you only searched, or read one file), **keep going** with more tools in the same turn until you can answer or have a clear blocker—do not stop at the first tool call unless it fully satisfies the request.
- **Model output:** If a response is mostly **function calls** without prose, that is normal—execute tools, then synthesize a clear **text** answer for the user once you have enough information.

## Risk and permissions
- Destructive or irreversible actions (deletes, force pushes, anything that wipes data) deserve a clear, honest description; the runtime may require explicit user approval. If the session uses **inline** approval, wait for it—do not instruct the user to "re-run with --yes" unless that is actually required by the environment.
- If a tool call is denied, **do not** immediately retry the identical call; adjust the plan or explain the blocker.

## Communication
- Before the first tool call in a turn, give a **short** line on what you are about to do. Assume the user does not see raw tool internals—summarize outcomes in plain language.
- Prefer small, testable edits and accurate reporting over breadth.
- If the user pastes **UI copy** or noise (e.g. fragments of a webpage, marketing lines, or mixed headings), infer intent: they often want that clutter **removed or replaced** in source—read the file, then edit the real `page.tsx` (or relevant file), do not treat pasted UI strings as a dialogue prompt."""

  tool_manifest = build_tool_manifest(cfg)

  if tool_manifest:
    base = f"{base}\n\n{tool_manifest}"
  extra = _load_gemini_md(cfg.project_root)
  if extra.strip():
    return f"{base}\n\n## Project instructions (GEMINI.md)\n{extra}"
  return base


def build_root_agent(cfg: GemCodeConfig, extra_tools: list | None = None) -> LlmAgent:
  """Create the root LlmAgent with tools and callbacks (no Runner)."""
  tools = build_function_tools(cfg)
  if getattr(cfg, "enable_memory", False):
    # ADK preload_memory injects retrieved memories into the next llm_request.
    from google.adk.tools import preload_memory

    tools = [preload_memory, *tools]
  if extra_tools:
    tools = [*tools, *extra_tools]

  before_model = _chain_before_model_callbacks(
      make_before_model_autocompact_callback(cfg),
      make_before_model_context_shrink_callback(cfg),
      make_before_model_callback(cfg),
      make_before_model_limits_callback(cfg),
      make_before_model_token_budget_callback(cfg),
  )
  cb_kwargs: dict = {
    "before_tool_callback": make_before_tool_callback(cfg),
    "after_tool_callback": make_after_tool_callback(cfg),
    "after_model_callback": make_after_model_callback(cfg),
    "on_tool_error_callback": make_on_tool_error_callback(cfg),
    "on_model_error_callback": make_on_model_error_callback(cfg),
  }
  if before_model is not None:
    cb_kwargs["before_model_callback"] = before_model

  # Claude-like thinking: enabled by default (Gemini dynamic), but allow
  # explicit overrides for disable/budgets/levels.
  gen_cfg = None
  thinking_cfg = build_thinking_config(cfg)
  tool_cfg = None
  model_id = getattr(cfg, "model", "") or ""
  is_gemini_3 = "gemini-3" in model_id.lower()
  comb_mode = (getattr(cfg, "tool_combination_mode", None) or "deep_research").lower()
  enable_for_run = False
  if comb_mode in ("auto", "deep_research"):
    enable_for_run = bool(getattr(cfg, "enable_deep_research", False))
  elif comb_mode == "always":
    enable_for_run = True
  elif comb_mode == "never":
    enable_for_run = False
  else:
    # Unknown values: stay conservative.
    enable_for_run = bool(getattr(cfg, "enable_deep_research", False))

  if enable_for_run and is_gemini_3:
    from google.genai import types

    # Gemini "tool context circulation" enables built-in tools results to
    # be combined with your client-side function tools in the same workflow.
    tool_cfg = types.ToolConfig(include_server_side_tool_invocations=True)

  if thinking_cfg is not None or tool_cfg is not None:
    from google.genai import types

    gen_cfg = types.GenerateContentConfig(
      thinking_config=thinking_cfg,
      tool_config=tool_cfg,
    )

  return LlmAgent(
      model=cfg.model,
      name="gemcode",
      instruction=build_instruction(cfg),
      tools=tools,
      generate_content_config=gen_cfg,
      **cb_kwargs,
  )


def create_runner(cfg: GemCodeConfig, extra_tools: list | None = None):
  """Backward-compatible: prefer `gemcode.session_runtime.create_runner`."""
  from gemcode.session_runtime import create_runner as _cr

  return _cr(cfg, extra_tools=extra_tools)
