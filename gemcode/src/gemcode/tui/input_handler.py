"""
Interactive input handler for the GemCode REPL.

Provides (when prompt_toolkit is available):
- Slash command autocomplete — type / and see all commands with descriptions;
  use ↑ / ↓ arrows to navigate the list, Tab or Enter to accept, Esc to dismiss.
- Conversation history — ↑ / ↓ arrows cycle through previous messages (when
  the completion popup is not open).
- Status bar — shows the active model and session ID while typing.
- Styled ❯ prompt in GemCode blue.

Falls back gracefully to plain input() when prompt_toolkit is unavailable or
stdout is not a TTY (e.g. piped input, CI).
"""

from __future__ import annotations

import os
import sys
from typing import Callable

# ---------------------------------------------------------------------------
# Availability guard — import everything inside try so we never hard-crash
# ---------------------------------------------------------------------------
_PT_AVAILABLE = False
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.document import Document  # noqa: F401 (type hint only)
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style

    _PT_AVAILABLE = True
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Slash command registry
# (name without /, description shown in the autocomplete popup)
# ---------------------------------------------------------------------------
SLASH_COMMANDS: list[tuple[str, str]] = [
    ("help",        "List all available commands"),
    ("init",        "Analyze project and generate GEMINI.md project instructions"),
    ("cost",        "Show session token usage and estimated USD cost breakdown"),
    ("notes",       "View agent auto-generated project notes (.gemcode/notes.md)"),
    ("diff",        "Show git diff (or checkpoint diff fallback)"),
    ("rewind",      "Restore files to a previous checkpoint  ·  alias: /checkpoint"),
    ("add-dir",     "Add extra directory for read/search access  ·  /add-dir list"),
    ("batch",       "Parallel large-change orchestrator (built-in GemSkill)"),
    ("review",      "Parallel code review: security + style + correctness simultaneously"),
    ("compact",     "Compact conversation history to free context window"),
    ("clear",       "Start a fresh session (clears history)  ·  alias: /session new"),
    ("model",       "View or switch model  ·  /model use <id>  ·  /model list"),
    ("mode",        "Set model mode  ·  /mode fast|balanced|quality|auto"),
    ("thinking",    "Thinking config  ·  /thinking verbose  ·  /thinking brief  ·  /thinking budget <N>"),
    ("status",      "Show full session status: model, capabilities, thinking, limits"),
    ("context",     "Show context window usage and token counts"),
    ("compact",     "Summarise conversation history to reclaim context space"),
    ("research",    "Toggle Google Search + URL Context  ·  /research on|off"),
    ("embeddings",  "Toggle semantic file search via Embeddings API  ·  /embeddings on|off"),
    ("computer",    "Browser automation (Playwright Chromium)  ·  /computer on|off|url"),
    ("caps",        "View/toggle all capabilities  ·  /caps  ·  /caps all  ·  /caps reset"),
    ("memory",      "Toggle persistent memory  ·  /memory on|off"),
    ("budget",      "Set per-turn token budget  ·  /budget <N>  ·  /budget off"),
    ("limits",      "Show/set execution limits (max_llm_calls, context, etc.)"),
    ("kaira",       "Background parallel job scheduler — how to run gemcode kaira"),
    ("code",        "Toggle sandboxed Python code executor (ADK BuiltInCodeExecutor)"),
    ("plan",        "Toggle plan mode — agent writes explicit plan before executing tools"),
    ("tools",       "List all tools and their permission categories"),
    ("config",      "Show full active configuration (all fields)"),
    ("permissions", "Show current permission mode (default / strict / yes)"),
    ("session",     "Show current session ID  ·  /session new to reset"),
    ("audit",       "Show recent audit log  ·  /audit [N lines]"),
    ("doctor",      "Run diagnostics and validate the environment"),
    ("hooks",       "Show post-turn hook configuration"),
    ("version",     "Show installed GemCode version"),
    ("exit",        "Exit GemCode"),
]


# ---------------------------------------------------------------------------
# Completer
# ---------------------------------------------------------------------------
if _PT_AVAILABLE:
    class _SlashCommandCompleter(Completer):
        """
        Activates when the input starts with '/'.
        Supports prefix matching and substring matching so typing '/mo' shows
        /model, '/compact', etc.
        """

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if not text.startswith("/"):
                return
            query = text[1:].lower()
            for name, desc in SLASH_COMMANDS:
                if not query or name.startswith(query) or query in name:
                    yield Completion(
                        "/" + name,
                        start_position=-len(text),
                        # Use GemCode's prompt blue, not the terminal "ansiblue" alias.
                        display=HTML(f'<style fg="#5fafd7">/<b>{name}</b></style>'),
                        display_meta=desc,
                    )


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------
class GemCodeInputHandler:
    """
    Wraps prompt_toolkit PromptSession with GemCode-specific styling and slash
    command autocomplete.  Use ``await handler.prompt_async()`` in the async
    REPL loop.

    When prompt_toolkit is not installed (or stdout is not a TTY) every call
    transparently delegates to ``input()``.
    """

    def __init__(
        self,
        *,
        ansi_enabled: bool = True,
        get_model: Callable[[], str] | None = None,
        get_session_id: Callable[[], str] | None = None,
        get_cfg: Callable[[], object | None] | None = None,
    ) -> None:
        self._ansi = ansi_enabled
        self._get_model = get_model or (lambda: "gemini")
        self._get_session_id = get_session_id or (lambda: "")
        self._get_cfg = get_cfg or (lambda: None)
        self._session: "PromptSession | None" = None

        if _PT_AVAILABLE and sys.stdin.isatty() and sys.stdout.isatty():
            self._build_session()

    def _build_session(self) -> None:
        style = Style.from_dict(
            {
                # The ❯ prompt glyph
                "prompt":                               "#5fafd7 bold",
                # Completion popup rows
                "completion-menu.completion":           "bg:#0d2035 fg:#5fafd7",
                "completion-menu.completion.current":   "bg:#0087d7 fg:#ffffff bold",
                # Meta (description) column
                "completion-menu.meta.completion":      "bg:#0d2035 fg:#5a7a9a",
                "completion-menu.meta.completion.current": "bg:#0065a0 fg:#cce8ff",
                "completion-menu.border":               "#0087d7",
                # Bottom status line — muted (not multi-colour); detail in HTML
                "bottom-toolbar":                       "bg:#0a0e12 fg:#5a6570",
            }
        )

        get_model = self._get_model
        get_session_id = self._get_session_id
        get_cfg = self._get_cfg

        def bottom_toolbar() -> HTML:
            model = get_model() or "gemini"
            sid = get_session_id()
            sid_short = sid[:8] if len(sid) >= 8 else sid
            cfg = get_cfg()
            extras = ""
            if cfg is not None:
                flags = []
                if getattr(cfg, "plan_mode", False):
                    flags.append("PLAN")
                if getattr(cfg, "enable_code_executor", False):
                    flags.append("CODE")
                if getattr(cfg, "enable_deep_research", False):
                    flags.append("RESEARCH")
                if getattr(cfg, "enable_computer_use", False):
                    flags.append("BROWSER")
                if flags:
                    extras = "  ·  " + "  ".join(f"[{f}]" for f in flags)
            # Single muted tone (no bright cyan) — reads as a quiet status strip
            return HTML(
                f'<style bg="#0a0e12" fg="#5f6b78"> ◆ {model}  ·  session {sid_short}'
                f'{extras}  ·  / for commands</style>'
            )

        kb = KeyBindings()

        @kb.add("c-c")
        def _ctrl_c(event):
            """Ctrl+C clears current line (like a real shell)."""
            event.app.current_buffer.reset()

        @kb.add("enter")
        def _submit(event):
            """Enter always submits — even in multiline mode (for pasted code)."""
            event.current_buffer.validate_and_handle()

        @kb.add("escape", "enter", eager=True)
        @kb.add("c-j")  # Ctrl+J = manual newline inside a message
        def _newline(event):
            """Meta+Enter or Ctrl+J inserts a real newline without submitting."""
            event.current_buffer.insert_text("\n")

        # Rows reserved for the / command popup.
        try:
            _menu_rows = int(os.environ.get("GEMCODE_TUI_RESERVE_MENU_LINES", "12"))
        except ValueError:
            _menu_rows = 12
        _menu_rows = max(6, min(24, _menu_rows))

        self._session = PromptSession(
            history=InMemoryHistory(),
            completer=_SlashCommandCompleter(),
            complete_while_typing=True,
            style=style,
            bottom_toolbar=bottom_toolbar,
            key_bindings=kb,
            mouse_support=False,
            complete_in_thread=True,
            reserve_space_for_menu=_menu_rows,
            complete_style="COLUMN",
            # Multiline=True so pasted code (with \n) lands in the buffer
            # as one block rather than submitting line-by-line.
            # Our Enter binding above overrides the default "add newline"
            # behaviour so single-line prompts work exactly as before.
            multiline=True,
        )

    def is_interactive(self) -> bool:
        return self._session is not None

    async def prompt_async(self) -> str:
        """
        Async prompt — returns stripped user input.
        Raises EOFError on Ctrl+D (caller should handle gracefully).
        """
        if self._session is not None:
            from prompt_toolkit.formatted_text import HTML as _HTML

            result = await self._session.prompt_async(
                _HTML('<style fg="#5fafd7"><b>❯</b></style> '),
            )
            return (result or "").strip()
        else:
            return input("❯ ").strip()
