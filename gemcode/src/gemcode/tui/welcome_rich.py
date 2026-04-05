"""Rich renderables for the scrollback welcome screen."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gemcode.tui.welcome_banner import format_welcome_banner

if TYPE_CHECKING:
  from rich.console import Console


def print_welcome_dashboard(cfg, *, console: Console) -> None:
  from rich.console import Group
  from rich.text import Text

  tw = max(60, console.width or 80)
  raw = format_welcome_banner(cfg, term_width=tw)
  rows: list[Text] = []
  for ln in raw.splitlines():
    low = ln.lower()
    if "█" in ln:
      rows.append(Text(ln, style="bold #3BA8E8"))
    elif any(c in ln for c in "╔╗║╚╠╣═│") and "█" not in ln:
      rows.append(Text(ln, style="bright_blue"))
    elif "gemcode v" in low:
      rows.append(Text(ln, style="bold dim"))
    elif "✦" in ln:
      rows.append(Text(ln, style="dim italic"))
    elif ln.strip().startswith("Tip:"):
      rows.append(Text(ln, style="dim"))
    else:
      rows.append(Text(ln))
  console.print(Group(*rows))


def print_shortcuts_hint(*, console: Console) -> None:
  from rich.text import Text

  console.print(Text("  ? for shortcuts", style="dim"))
