"""Environment and CLI configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _split_csv(s: str | None) -> list[str]:
  if not s:
    return []
  return [x.strip() for x in s.split(",") if x.strip()]


def _opt_positive_int(name: str) -> int | None:
  v = os.environ.get(name)
  if v is None or not str(v).strip():
    return None
  n = int(str(v).strip())
  return n if n > 0 else None


def _opt_int(name: str) -> int | None:
  """Optional int from env, allowing 0 / negative values."""
  v = os.environ.get(name)
  if v is None or not str(v).strip():
    return None
  return int(str(v).strip())


def _max_context_chars_from_env() -> int:
  """Unset → 400k chars; 0 disables context shrink; invalid values fall back to default."""
  raw = os.environ.get("GEMCODE_MAX_CONTEXT_CHARS")
  if raw is None or not str(raw).strip():
    return 400_000
  try:
    n = int(str(raw).strip())
  except ValueError:
    return 400_000
  return max(0, n)


def _truthy_env(name: str, *, default: bool = False) -> bool:
  v = os.environ.get(name)
  if v is None:
    return default
  return v.lower() in ("1", "true", "yes", "on")


def token_budget_invocation_reset() -> dict:
  """Reset per-user-message token budget tracker (matches new `query()` in Claude)."""
  import time

  t = int(time.time() * 1000)
  return {
      "gemcode:bt_cc": 0,
      "gemcode:bt_ld": 0,
      "gemcode:bt_lg": 0,
      "gemcode:bt_t0": t,
      "gemcode:bt_base_total_tokens": -1,
      "gemcode:bt_token_budget_stop": False,
      # Tool-result aggregate budget (per user message)
      "gemcode:tool_group_chars": 0,
      "gemcode:tool_group_budget_exceeded": False,
      "gemcode:tool_seq": 0,
  }


@dataclass
class GemCodeConfig:
  """Runtime options (CLI + env)."""

  project_root: Path
  model: str = field(default_factory=lambda: os.environ.get("GEMCODE_MODEL", "gemini-3.1-pro-preview"))
  # Model mode: fast|balanced|quality|auto
  model_mode: str = field(
    default_factory=lambda: os.environ.get("GEMCODE_MODEL_MODE", "fast")
  )
  model_quality: str | None = field(
    default_factory=lambda: os.environ.get("GEMCODE_MODEL_QUALITY")
  )
  model_balanced: str | None = field(
    default_factory=lambda: os.environ.get("GEMCODE_MODEL_BALANCED")
  )
  # Model family routing: choose between the "primary" model ids (GEMCODE_MODEL*
  # fields) and 2.5 alternatives (GEMCODE_MODEL_ALT* fields).
  #
  # - `auto`: heuristic chooses primary for complex prompts, alt for simple
  # - `primary`: always use primary ids
  # - `alt`: always use 2.5 ids (GEMCODE_MODEL_ALT*)
  model_family_mode: str = field(
    default_factory=lambda: os.environ.get("GEMCODE_MODEL_FAMILY_MODE", "auto")
  )
  model_alt: str | None = field(
    default_factory=lambda: os.environ.get("GEMCODE_MODEL_ALT")
  )
  model_alt_quality: str | None = field(
    default_factory=lambda: os.environ.get("GEMCODE_MODEL_ALT_QUALITY")
  )
  model_alt_balanced: str | None = field(
    default_factory=lambda: os.environ.get("GEMCODE_MODEL_ALT_BALANCED")
  )
  permission_mode: str = field(
    default_factory=lambda: os.environ.get("GEMCODE_PERMISSION_MODE", "default")
  )
  allow_commands: frozenset[str] | None = None
  yes_to_all: bool = False
  # When enabled, GemCode will ask for user confirmation in the *same run*
  # (HITL) before mutating tools / computer-use tools execute.
  #
  # Default behavior is controlled in the CLI:
  # - If `GEMCODE_INTERACTIVE_PERMISSION_ASK` is set, we honor it.
  # - Otherwise we enable when stdin is a TTY and `--yes` is not provided.
  interactive_permission_ask: bool = field(
    default_factory=lambda: _truthy_env(
      "GEMCODE_INTERACTIVE_PERMISSION_ASK", default=False
    )
  )
  # After the user approves one HITL prompt, skip further prompts until a new session.
  # (ADK otherwise asks once per tool call.) Set GEMCODE_HITL_STICKY_SESSION=0 to disable.
  interactive_hitl_sticky_session: bool = field(
    default_factory=lambda: _truthy_env(
      "GEMCODE_HITL_STICKY_SESSION", default=True
    )
  )
  max_content_items: int = field(
    default_factory=lambda: int(os.environ.get("GEMCODE_MAX_CONTENT_ITEMS", "40"))
  )
  # Cap long string fields in tool results before they enter session history.
  tool_result_max_chars: int = field(
    default_factory=lambda: max(
        1000,
        int(os.environ.get("GEMCODE_TOOL_RESULT_MAX_CHARS", "12000")),
    )
  )

  # Aggregate tool-result budget per user message (approx. characters of tool payloads).
  # When exceeded, GemCode tightens subsequent tool output caps for the remainder of the turn.
  tool_result_group_budget_chars: int = field(
    default_factory=lambda: int(os.environ.get("GEMCODE_TOOL_RESULT_GROUP_BUDGET_CHARS", "60000"))
  )

  # When enabled, oversized tool outputs are offloaded to disk under
  # .gemcode/tool-results/ and replaced in history with stable refs + previews.
  # This reduces context bloat and improves prompt-cache stability.
  tool_result_offload_enabled: bool = field(
    default_factory=lambda: _truthy_env("GEMCODE_TOOL_RESULT_OFFLOAD", default=True)
  )

  # Dynamic token policy: adapt tool output caps to context pressure so we stay
  # cheap when context is tight, but remain evidence-rich when there's room.
  dynamic_token_policy: bool = field(
    default_factory=lambda: _truthy_env("GEMCODE_DYNAMIC_TOKEN_POLICY", default=True)
  )

  # Dynamic risk policy: boosts caps when the current task appears risky/complex.
  dynamic_risk_policy: bool = field(
    default_factory=lambda: _truthy_env("GEMCODE_DYNAMIC_RISK_POLICY", default=True)
  )

  # How much the risk score can expand caps (0.0-1.5 reasonable).
  dynamic_risk_boost: float = field(
    default_factory=lambda: float(os.environ.get("GEMCODE_DYNAMIC_RISK_BOOST", "0.6"))
  )
  # Trim oldest text in llm_request.contents when over budget (see context_budget.py).
  context_shrink_enabled: bool = field(
    default_factory=lambda: _truthy_env("GEMCODE_CONTEXT_SHRINK", default=True)
  )
  max_context_chars: int = field(default_factory=_max_context_chars_from_env)
  # ADK RunConfig.max_llm_calls. Unset env → __post_init__ defaults to 256 per user message.
  max_llm_calls: int | None = field(default_factory=lambda: _opt_positive_int("GEMCODE_MAX_LLM_CALLS"))
  # Hard stop before next LLM call when cumulative usage_metadata totals exceed this.
  max_session_tokens: int | None = field(
    default_factory=lambda: _opt_positive_int("GEMCODE_MAX_SESSION_TOKENS")
  )
  # Optional per-turn style budget for continuation logging (see query/token_budget.py).
  token_budget: int | None = field(default_factory=lambda: _opt_positive_int("GEMCODE_TOKEN_BUDGET"))
  # Enables persistent memory via ADK context integration (file-backed).
  enable_memory: bool = field(
    default_factory=lambda: _truthy_env("GEMCODE_ENABLE_MEMORY", default=False)
  )

  # Modality toggles (tool injection + routing).
  enable_deep_research: bool = field(
    default_factory=lambda: _truthy_env("GEMCODE_ENABLE_DEEP_RESEARCH", default=False)
  )
  enable_embeddings: bool = field(
    default_factory=lambda: _truthy_env("GEMCODE_ENABLE_EMBEDDINGS", default=False)
  )

  # Deep research model id used when routing selects deep research.
  model_deep_research: str = field(
    default_factory=lambda: os.environ.get(
      "GEMCODE_MODEL_DEEP_RESEARCH", "travel_explore"
    )
  )

  # Embeddings model id used by embeddings-powered tools/memory (if enabled).
  embeddings_model: str = field(
    default_factory=lambda: os.environ.get(
      "GEMCODE_EMBEDDINGS_MODEL", "models/gemini-embedding-2-preview"
    )
  )

  # Deep research: Google Maps grounding is optional because it can be
  # incompatible with other built-in tools (e.g., google_search) in the same
  # request depending on the model/tooling layer.
  enable_maps_grounding: bool = field(
    default_factory=lambda: _truthy_env("GEMCODE_ENABLE_MAPS_GROUNDING", default=False)
  )

  # Computer use (ADK ComputerUseToolset) enable/disable; default is off for safety.
  enable_computer_use: bool = field(
    default_factory=lambda: _truthy_env("GEMCODE_ENABLE_COMPUTER_USE", default=False)
  )

  # Audio mode (Gemini Live models). Only fully supported via `gemcode live-audio`
  # in this MVP.
  enable_audio: bool = field(
    default_factory=lambda: _truthy_env("GEMCODE_ENABLE_AUDIO", default=False)
  )
  model_audio_live: str = field(
    default_factory=lambda: os.environ.get(
      "GEMCODE_MODEL_AUDIO_LIVE", "gemini-3.1-flash-live-preview"
    )
  )
  model_computer_use: str = field(
    default_factory=lambda: os.environ.get(
      "GEMCODE_MODEL_COMPUTER_USE",
      "gemini-2.5-computer-use-preview-10-2025",
    )
  )

  # Capability routing: auto|research|embeddings|computer|audio|all
  capability_mode: str = field(
    default_factory=lambda: os.environ.get("GEMCODE_CAPABILITY_MODE", "auto")
  )

  # Gemini 3 "tool context circulation" (built-in tools + function tools
  # combination). Controls when we set ToolConfig(include_server_side_tool_invocations=True).
  #
  # - deep_research: only when enable_deep_research is enabled
  # - always: enable for Gemini 3.x regardless of deep-research toggle
  # - never: disable always
  # - auto: alias for deep_research
  tool_combination_mode: str = field(
    default_factory=lambda: os.environ.get(
      "GEMCODE_TOOL_COMBINATION_MODE", "deep_research"
    )
  )

  # Set by CLI when the user explicitly provides --model. Used to prevent
  # role-based routing from overriding their selection.
  model_overridden: bool = False

  # Gemini thinking controls (Claude-like intent, Gemini-specific knobs).
  #
  # Claude Code enables thinking by default and only forces disable/budgets
  # when explicitly configured. We match that by returning "None" unless the
  # user asks for explicit overrides below.
  #
  # - Gemini 3.x: supports `thinkingLevel` (can't fully disable).
  # - Gemini 2.5: supports `thinkingBudget` (0 disables for models that allow it).
  disable_thinking: bool = field(
    default_factory=lambda: _truthy_env("GEMCODE_DISABLE_THINKING", default=False)
  )
  include_thought_summaries: bool = field(
    default_factory=lambda: _truthy_env(
      "GEMCODE_INCLUDE_THOUGHT_SUMMARIES", default=False
    )
  )
  thinking_level: str | None = field(
    default_factory=lambda: os.environ.get("GEMCODE_THINKING_LEVEL")
  )
  thinking_budget: int | None = field(
    default_factory=lambda: _opt_int("GEMCODE_THINKING_BUDGET")
  )

  # Controls how the TUI renders model thinking: True = full Rich Markdown,
  # False = collapsed one-line excerpt.
  # Toggled at runtime via /thinking verbose|brief.
  show_full_thinking: bool = field(
    default_factory=lambda: _truthy_env("GEMCODE_SHOW_FULL_THINKING", default=True)
  )

  # Enable ADK BuiltInCodeExecutor for safe sandboxed Python execution via
  # Gemini's code execution API. When on, the agent can write and run Python
  # snippets inline (math, data processing, quick tests) without requiring
  # bash/shell permissions. Requires a Gemini model that supports code execution
  # (gemini-2.5-flash, gemini-2.5-pro, gemini-3.x).
  enable_code_executor: bool = field(
    default_factory=lambda: _truthy_env("GEMCODE_ENABLE_CODE_EXECUTOR", default=False)
  )

  # Enable ADK artifact service for storing large/binary outputs (screenshots,
  # generated files, reports) outside of session history.
  # When on, the agent can save_artifact / load_artifact keyed by filename.
  enable_artifacts: bool = field(
    default_factory=lambda: _truthy_env("GEMCODE_ENABLE_ARTIFACTS", default=True)
  )

  # Plan mode: when ON, the agent explicitly writes out a numbered plan
  # BEFORE executing any tools, then checks the plan before reporting done.
  # Like OpenClaude's EnterPlanMode — great for complex, multi-file tasks.
  # Toggle at runtime with /plan on|off.
  plan_mode: bool = field(
    default_factory=lambda: _truthy_env("GEMCODE_PLAN_MODE", default=False)
  )

  # Always-on web search (independent of deep_research / research mode).
  # When True, google_search is available as a basic tool without enabling
  # the full deep_research capability suite (url_context, maps, etc.).
  enable_web_search: bool = field(
    default_factory=lambda: _truthy_env("GEMCODE_ENABLE_WEB_SEARCH", default=False)
  )

  def __post_init__(self) -> None:
    self.project_root = self.project_root.resolve()
    # Default agentic depth when env omits GEMCODE_MAX_LLM_CALLS (was: None → SDK default).
    if self.max_llm_calls is None and (
        os.environ.get("GEMCODE_MAX_LLM_CALLS") is None
        or not str(os.environ.get("GEMCODE_MAX_LLM_CALLS", "")).strip()
    ):
      self.max_llm_calls = 256
    if self.allow_commands is None:
      env = os.environ.get("GEMCODE_ALLOW_COMMANDS")
      if env:
        self.allow_commands = frozenset(_split_csv(env))
      else:
        self.allow_commands = frozenset(
          (
            # Python
            "pytest",
            "python3",
            "python",
            "pip",
            "pip3",
            "ruff",
            "uv",
            "mypy",
            "black",
            "isort",
            "flake8",
            "pylint",
            # Node / JS
            "npm",
            "npx",
            "node",
            "pnpm",
            "yarn",
            "bun",
            "bunx",
            "tsc",
            "eslint",
            "prettier",
            "vite",
            "next",
            "turbo",
            # Systems
            "cargo",
            "go",
            "rustc",
            "make",
            "cmake",
            "gradle",
            "mvn",
            "java",
            "javac",
            "dotnet",
            # Version control
            "git",
            "gh",
            # Shell utilities (safe read/inspect)
            "cat",
            "ls",
            "echo",
            "find",
            "head",
            "tail",
            "wc",
            "sort",
            "uniq",
            "cut",
            "awk",
            "sed",
            "grep",
            "rg",
            "ag",
            # File management
            "mkdir",
            "cp",
            "mv",
            "touch",
            "chmod",
            "chown",
            # Networking (read)
            "curl",
            "wget",
            # Containers
            "docker",
            "docker-compose",
            "kubectl",
            # Database
            "psql",
            "mysql",
            "sqlite3",
            # Misc
            "jq",
            "yq",
            "which",
            "env",
            "printenv",
            "date",
            "pwd",
            "diff",
            "patch",
            "zip",
            "unzip",
            "tar",
          )
        )


def load_dotenv_optional() -> None:
  try:
    from dotenv import load_dotenv

    load_dotenv()
  except ImportError:
    pass


def load_cli_environment() -> None:
  """
  Load local ``.env`` then apply persisted user API key (if env still unset).

  Precedence: explicit ``GOOGLE_API_KEY`` in the environment, then ``.env``,
  then ``~/.gemcode/credentials.json`` (see ``gemcode.credentials``).
  """
  load_dotenv_optional()
  from gemcode.credentials import apply_saved_google_api_key_to_environ

  apply_saved_google_api_key_to_environ()

  from gemcode.logging_config import apply_gemcode_logging_filters
  from gemcode.version import get_version

  os.environ.setdefault("GEMCODE_VERSION", get_version())
  apply_gemcode_logging_filters()
