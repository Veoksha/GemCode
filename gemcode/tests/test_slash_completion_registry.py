"""Slash command registry used by TUI + readline completion."""

from __future__ import annotations

from gemcode.repl_commands import SLASH_COMMANDS, install_readline_slash_completion


def test_slash_commands_registry_covers_new_commands() -> None:
  names = [n for n, _ in SLASH_COMMANDS]
  for required in (
      "append",
      "eval",
      "autotune",
      "curated",
      "login",
      "live-audio",
      "maps",
      "create",
      "gemskill",
      "style",
      "rules",
      "help",
      "attach",
  ):
    assert required in names, f"missing slash completion entry: {required}"


def test_slash_registry_omits_redundant_completion_aliases() -> None:
  """Aliases still work in repl_slash; they should not duplicate menu rows."""
  names = [n for n, _ in SLASH_COMMANDS]
  for redundant in (
      "logs",
      "map",
      "perm",
      "permission",
      "token-budget",
      "memoryfiles",
      "memory-files",
      "checkpoint",
      "embed",
      "models",
      "?",
      "quit",
  ):
    assert redundant not in names, f"unexpected duplicate entry: {redundant}"


def test_install_readline_slash_completion_does_not_raise() -> None:
  install_readline_slash_completion()
