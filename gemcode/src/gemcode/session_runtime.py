"""
Session runtime (Claude Code: outer engine ≈ QueryEngine + session store).

- **SqliteSessionService**: durable session + events (like transcript persistence).
- **Runner**: wires the root agent to the session, equivalent to “submit query → stream events”.

The inner turn loop (model ↔ tools) is implemented inside ADK (analogous to `query.ts` +
StreamingToolExecutor + runTools orchestration). See `gemcode.query` for transition types,
token budget helpers, and `GemCodeQueryEngine`.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

# Suppress ADK's noisy "EXPERIMENTAL feature" UserWarning globally.
# Users get enough context from gemcode's own messages; the warning is redundant.
warnings.filterwarnings("ignore", category=UserWarning, message=".*EXPERIMENTAL.*")

from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService

from gemcode.agent import build_root_agent
from gemcode.config import GemCodeConfig
from gemcode.modality_tools import build_extra_tools as build_modality_extra_tools
from gemcode.memory.embedding_memory_service import EmbeddingFileMemoryService
from gemcode.memory.file_memory_service import FileMemoryService
from gemcode.plugins.terminal_hooks_plugin import GemCodeTerminalHooksPlugin
from gemcode.plugins.tool_recovery_plugin import GemCodeReflectAndRetryToolPlugin


def session_db_path(cfg: GemCodeConfig) -> Path:
  return cfg.project_root / ".gemcode" / "sessions.sqlite"


def _wrap_computer_use_tools_with_safety_ack(llm_request) -> None:
  """
  Gemini Computer Use models may include `safety_decision` in tool call args.
  The client must acknowledge it in the corresponding FunctionResponse or the
  API returns HTTP 400.

  ADK's ComputerUseTool returns only image/url by default, so we wrap the tool
  functions to (a) ignore `safety_decision` for execution and (b) include
  `safety_acknowledgement="true"` in the tool result when present.
  """
  try:
    from google.adk.tools.computer_use.computer_use_tool import ComputerUseTool
  except Exception:
    return

  try:
    tools_dict = getattr(llm_request, "tools_dict", None)
    if not isinstance(tools_dict, dict) or not tools_dict:
      return
  except Exception:
    return

  # Wrap each ComputerUseTool's underlying function in-place.
  for tool_name, tool in list(tools_dict.items()):
    try:
      if not isinstance(tool, ComputerUseTool):
        continue
      original_func = getattr(tool, "func", None)
      if original_func is None:
        continue

      async def wrapped(*, _orig=original_func, _tool_name=tool_name, **args):
        sd = None
        if isinstance(args, dict) and "safety_decision" in args:
          sd = args.pop("safety_decision", None)
        result = await _orig(**args)
        if sd is None:
          return result
        # Acknowledge the safety decision as required by Gemini computer-use.
        if isinstance(result, dict):
          out = dict(result)
          out["safety_acknowledgement"] = "true"
          return out
        return {"result": result, "safety_acknowledgement": "true"}

      try:
        wrapped.__name__ = tool_name
      except Exception:
        pass
      tool.func = wrapped
    except Exception:
      continue


def _playwright_available() -> bool:
  """
  Quick synchronous check: does a usable Playwright browser executable exist?

  Runs playwright.sync_api.sync_playwright() briefly to resolve the browser
  path, without actually launching a browser. Falls back to a path probe if
  playwright is not installed.

  Returns True only when Playwright AND at least one browser binary are present.
  This is called before building the runner so we can disable computer-use
  early and prevent model routing from switching to gemini-2.5-computer-use-*.
  """
  # 1) Preferred: ask Playwright for the resolved executable path.
  # This can fail in some environment-mismatch cases (package installed but driver
  # cannot start), so we also do a cache-based probe below.
  try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
      exe = getattr(p.chromium, "executable_path", None)
      if exe and Path(str(exe)).exists():
        return True
  except Exception:
    pass

  # 2) Fallback: check the default browser cache directly.
  # This avoids false negatives when Playwright import/driver resolution is flaky,
  # but browsers are present (common on macOS with mixed --user/system installs).
  try:
    cache_root = Path.home() / "Library" / "Caches" / "ms-playwright"
    if not cache_root.exists():
      return False
    # macOS paths (Chromium.app or "Google Chrome for Testing.app")
    mac_bins = list(cache_root.glob("chromium-*/*/Chromium.app/Contents/MacOS/Chromium"))
    mac_bins += list(
      cache_root.glob(
        "chromium-*/*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
      )
    )
    if any(p.exists() for p in mac_bins):
      return True
  except Exception:
    pass

  return False


def _make_safe_computer_toolset(computer):
  """
  Build a BaseToolset-compatible wrapper around ComputerUseToolset that catches
  Playwright startup failures gracefully instead of crashing LlmAgent validation.

  Must be a proper BaseToolset subclass (not a plain class) because ADK's Pydantic
  model for LlmAgent validates each entry in `tools` against BaseTool | BaseToolset.
  Returns a real BaseToolset subclass instance, or None if BaseToolset is unavailable.
  """
  try:
    from google.adk.tools.base_toolset import BaseToolset
  except ImportError:
    return None

  class _SafeComputerUseToolset(BaseToolset):
    """Wraps ComputerUseToolset; degrades to a no-op if Playwright is missing."""

    def __init__(self) -> None:
      try:
        from google.adk.tools.computer_use.computer_use_toolset import ComputerUseToolset
        self._inner = ComputerUseToolset(computer=computer)
      except Exception:
        self._inner = None
      self._broken = False
      self._warned = False

    def _warn_once(self, error: Exception) -> None:
      if self._warned:
        return
      self._warned = True
      import sys
      msg = str(error)
      if "playwright install" in msg.lower() or "executable doesn't exist" in msg.lower():
        print(
          "\n[gemcode] Browser (computer-use) is unavailable — Playwright browsers are not installed.\n"
          "  Run:  playwright install chromium\n"
          "  Then restart GemCode with /computer on (or --computer flag).\n"
          "  Continuing without browser tools for this session.\n",
          file=sys.stderr,
        )
      else:
        print(
          f"\n[gemcode] Browser (computer-use) failed to start: {msg!s:.200}\n"
          "  Continuing without browser tools for this session.\n",
          file=sys.stderr,
        )

    # ── BaseToolset required attribute ──────────────────────────────────────
    @property
    def tool_name_prefix(self) -> str:
      if self._inner is not None:
        try:
          return self._inner.tool_name_prefix
        except Exception:
          pass
      return ""

    # ── Core protocol methods ────────────────────────────────────────────────

    async def process_llm_request(self, *, tool_context, llm_request) -> None:
      if self._broken or self._inner is None:
        return
      try:
        await self._inner.process_llm_request(
            tool_context=tool_context, llm_request=llm_request
        )
        _wrap_computer_use_tools_with_safety_ack(llm_request)
      except Exception as exc:
        if not self._broken:
          self._broken = True
          self._warn_once(exc)

    async def get_tools(self, readonly_context=None):
      if self._broken or self._inner is None:
        return []
      try:
        return await self._inner.get_tools(readonly_context)
      except Exception as exc:
        if not self._broken:
          self._broken = True
          self._warn_once(exc)
        return []

    async def close(self) -> None:
      if self._inner is not None:
        try:
          await self._inner.close()
        except Exception:
          pass

    def __getattr__(self, name: str):
      """Proxy any other BaseToolset attributes ADK needs to the inner toolset."""
      # Guard against infinite recursion for our own private attrs.
      if name.startswith("_"):
        raise AttributeError(name)
      inner = object.__getattribute__(self, "_inner")
      if inner is not None:
        try:
          return getattr(inner, name)
        except AttributeError:
          pass
      raise AttributeError(f"'_SafeComputerUseToolset' has no attribute '{name}'")

  return _SafeComputerUseToolset()


def _build_artifact_service(cfg: GemCodeConfig):
  """
  Return an ADK ArtifactService for this session, or None if disabled.

  Uses InMemoryArtifactService so artifacts are available within the session
  without requiring GCS credentials. The agent can save screenshots, generated
  files, large reports, etc. as artifacts to avoid bloating session history.
  """
  if not getattr(cfg, "enable_artifacts", True):
    return None
  try:
    from google.adk.artifacts import InMemoryArtifactService
    return InMemoryArtifactService()
  except Exception:
    return None


def create_runner(cfg: GemCodeConfig, extra_tools: list | None = None) -> Runner:
  """Construct Runner + SQLite session service + root LlmAgent."""
  modality_tools = build_modality_extra_tools(cfg)
  merged_extra_tools: list | None
  if extra_tools:
    merged_extra_tools = [*extra_tools, *modality_tools] if modality_tools else list(extra_tools)
  else:
    merged_extra_tools = modality_tools or None

  # ── MCP toolsets from .gemcode/mcp.json ─────────────────────────────────
  # Supports stdio, http (Streamable HTTP), and sse connection types.
  try:
    from gemcode.mcp_loader import load_mcp_toolsets
    mcp_tools = load_mcp_toolsets(cfg)
    if mcp_tools:
      merged_extra_tools = list(merged_extra_tools or []) + mcp_tools
  except Exception:
    pass  # MCP not installed or mcp.json invalid — continue without

  # ── OpenAPI toolsets from .gemcode/openapi/ ──────────────────────────────
  # Drop any *.yaml / *.json OpenAPI spec in .gemcode/openapi/ to auto-generate
  # REST API tools for that service (GitHub, Sentry, internal APIs, etc.)
  try:
    from gemcode.openapi_loader import load_openapi_toolsets
    oa_tools = load_openapi_toolsets(cfg.project_root)
    if oa_tools:
      merged_extra_tools = list(merged_extra_tools or []) + oa_tools
  except Exception:
    pass  # OpenAPIToolset not in this ADK version — continue without

  # Computer-use: ADK ComputerUseToolset backed by our Playwright BrowserComputer.
  # Probe Playwright BEFORE building the agent so model routing (which runs
  # inside build_root_agent → pick_effective_model) never switches to
  # gemini-2.5-computer-use-* when the browser binary is missing.
  if getattr(cfg, "enable_computer_use", False):
    if not _playwright_available():
      import sys
      print(
        "\n[gemcode] Browser (computer-use) is unavailable — Playwright browsers "
        "are not installed.\n"
        "  Run:  playwright install chromium\n"
        "  Then restart GemCode with /computer on (or --computer flag).\n"
        "  Disabling computer-use for this session.\n",
        file=sys.stderr,
      )
      # Disable so model_routing stays on the normal model (not computer-use preview).
      cfg.enable_computer_use = False
      cfg._computer_use_available = False  # type: ignore[attr-defined]
      # If the TUI already routed the model to the computer-use preview model for
      # this turn, reset back to the normal model so the next agent construction
      # doesn't keep using a model that requires the missing tool.
      try:
        if not getattr(cfg, "model_overridden", False) and cfg.model == cfg.model_computer_use:
          cfg.model = os.environ.get("GEMCODE_MODEL", "gemini-3.1-pro-preview")
      except Exception:
        pass
    else:
      cfg._computer_use_available = True  # type: ignore[attr-defined]
      headless_env = os.environ.get("GEMCODE_COMPUTER_HEADLESS", "1").lower()
      headless = headless_env in ("1", "true", "yes", "on")
      viewport_w = int(os.environ.get("GEMCODE_BROWSER_WIDTH", "1280"))
      viewport_h = int(os.environ.get("GEMCODE_BROWSER_HEIGHT", "720"))
      from gemcode.computer_use.browser_computer import BrowserComputer

      computer = BrowserComputer(
        headless=headless,
        viewport_size=(viewport_w, viewport_h),
      )
      computer_toolset = _make_safe_computer_toolset(computer)
      merged_extra_tools = list(merged_extra_tools or [])
      if computer_toolset is not None:
        merged_extra_tools.append(computer_toolset)

      # Standalone read-only browser tools (browser_screenshot, browser_get_text, etc.)
      from gemcode.tools.browser import build_browser_inspection_tools
      browser_tools = build_browser_inspection_tools(cfg, computer)
      merged_extra_tools.extend(browser_tools)

      # Store reference on cfg so slash commands / TUI can check browser state.
      cfg._browser_computer = computer  # type: ignore[attr-defined]

  agent = build_root_agent(cfg, extra_tools=merged_extra_tools)
  db = session_db_path(cfg)
  db.parent.mkdir(parents=True, exist_ok=True)
  session_service = SqliteSessionService(str(db))

  plugins = [GemCodeTerminalHooksPlugin(cfg)]
  # Place recovery plugin before terminal hooks so it can influence tool results
  # during the invocation.
  if True:
    plugins.insert(0, GemCodeReflectAndRetryToolPlugin(cfg))
  memory_service = None
  if getattr(cfg, "enable_memory", False):
    mem_path = cfg.project_root / ".gemcode" / "memories.jsonl"
    if getattr(cfg, "enable_embeddings", False):
      memory_service = EmbeddingFileMemoryService(
        mem_path, embeddings_model=getattr(cfg, "embeddings_model", None)
      )
    else:
      memory_service = FileMemoryService(mem_path)

  artifact_service = _build_artifact_service(cfg)

  runner_kwargs: dict = dict(
      app_name="gemcode",
      agent=agent,
      session_service=session_service,
      plugins=plugins,
      memory_service=memory_service,
      auto_create_session=True,
  )
  if artifact_service is not None:
    runner_kwargs["artifact_service"] = artifact_service

  return Runner(**runner_kwargs)
