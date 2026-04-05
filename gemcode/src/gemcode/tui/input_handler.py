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
    ("clear",       "Start a fresh session — clears conversation history"),
    ("model",       "View or switch model  ·  /model use <id>  ·  /model list"),
    ("status",      "Show session ID, model, and current settings"),
    ("context",     "Show context window usage and token counts"),
    ("tools",       "List all tools and their permission categories"),
    ("compact",     "Summarise conversation history to reclaim context space"),
    ("config",      "Show active configuration and environment variables"),
    ("permissions", "Show current permission mode (default / strict / yes)"),
    ("thinking",    "View or change thinking config  ·  /thinking level <low|medium|high>  ·  /thinking budget <N>"),
    ("audit",       "Show recent audit log  ·  /audit [N lines]"),
    ("memory",      "Show memory / storage settings"),
    ("doctor",      "Run diagnostics and validate the environment"),
    ("hooks",       "Show hooks configuration"),
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
                        display=HTML(f"<ansiblue>/<b>{name}</b></ansiblue>"),
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
    ) -> None:
        self._ansi = ansi_enabled
        self._get_model = get_model or (lambda: "gemini")
        self._get_session_id = get_session_id or (lambda: "")
        self._session: "PromptSession | None" = None

        if _PT_AVAILABLE and sys.stdin.isatty() and sys.stdout.isatty():
            self._build_session()

    def _build_session(self) -> None:
        style = Style.from_dict(
            {
                # The ❯ prompt glyph
                "prompt":                               "#5fafd7 bold",
                # Completion popup rows
                "completion-menu.completion":           "bg:#0d2035 fg:#87cefa",
                "completion-menu.completion.current":   "bg:#0087d7 fg:#ffffff bold",
                # Meta (description) column
                "completion-menu.meta.completion":      "bg:#0d2035 fg:#5a7a9a",
                "completion-menu.meta.completion.current": "bg:#0065a0 fg:#cce8ff",
                "completion-menu.border":               "#0087d7",
                # Bottom toolbar
                "bottom-toolbar":                       "bg:#0d1f2d fg:#4a7a9a",
            }
        )

        get_model = self._get_model
        get_session_id = self._get_session_id

        def bottom_toolbar() -> HTML:
            model = get_model() or "gemini"
            sid = get_session_id()
            sid_short = sid[:8] if len(sid) >= 8 else sid
            return HTML(
                f'<style bg="#0d1f2d" fg="#5fafd7"><b> ◆ {model}</b></style>'
                f'<style bg="#0d1f2d" fg="#3d6080">  ·  session {sid_short}'
                f'  ·  / for commands</style>'
            )

        kb = KeyBindings()

        @kb.add("c-c")
        def _ctrl_c(event):
            """Ctrl+C clears current line (like a real shell)."""
            event.app.current_buffer.reset()

        self._session = PromptSession(
            history=InMemoryHistory(),
            completer=_SlashCommandCompleter(),
            complete_while_typing=True,
            style=style,
            bottom_toolbar=bottom_toolbar,
            key_bindings=kb,
            mouse_support=False,
            complete_in_thread=True,
            reserve_space_for_menu=10,
            # Show completion descriptions to the right (like VS Code)
            complete_style="COLUMN",
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
