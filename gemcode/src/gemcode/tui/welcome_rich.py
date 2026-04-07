"""Rich renderables for the scrollback welcome screen."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gemcode.tui.welcome_banner import format_welcome_banner

# Classic terminal blues (xterm 75 / 33); gradient stays in this range (no violet).
_BLUE_MAIN = "#5fafd7"
_BLUE_DEEP = "#0087d7"
_BLUE_HI = "#87d7ff"  # lighter edge of gradient
# Info box: slate grey frame + labels; cyan-blue values (not saturated “blue” that reads purple).
_BORDER_GREY = "#64748b"
_LABEL_GREY = "#94a3b8"
_VALUE_BLUE = "#5fafd7"

if TYPE_CHECKING:
  from rich.console import Console


def _hex_rgb(h: str) -> tuple[int, int, int]:
  h = h.removeprefix("#")
  return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lerp_rgb(
    a: tuple[int, int, int], b: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
  t = max(0.0, min(1.0, t))
  return (
      round(a[0] + (b[0] - a[0]) * t),
      round(a[1] + (b[1] - a[1]) * t),
      round(a[2] + (b[2] - a[2]) * t),
  )


def _gradient_logo_line(line: str, *, line_t: float) -> "Text":
  """Horizontal + mild vertical blend (same idea as reference terminal UI startup gradient)."""
  from rich.text import Text

  lo = _hex_rgb(_BLUE_DEEP)
  hi = _hex_rgb(_BLUE_HI)
  n = len(line)
  out = Text()
  for i, ch in enumerate(line):
    if ch == " ":
      out.append(" ")
      continue
    t_h = i / (n - 1) if n > 1 else 0.0
    blend = line_t * 0.45 + t_h * 0.55
    r, g, b = _lerp_rgb(lo, hi, blend)
    out.append(ch, style=f"bold #{r:02x}{g:02x}{b:02x}")
  return out


_BOX_DRAW = frozenset("╔╗║╚╠╣═")


def _info_box_line(line: str) -> "Text":
  """Grey borders/labels, blue values — avoids an all-blue/violet panel."""
  from rich.text import Text

  t = Text()
  first = line.find("│")
  last = line.rfind("│")
  if first != -1 and last > first:
    t.append(line[:first])
    t.append("│", style=f"dim {_BORDER_GREY}")
    inner = line[first + 1:last]
    stripped = inner.lstrip()
    if stripped.startswith("●") and "Ready" in inner:
      ready_at = inner.find("Ready")
      if ready_at > 0:
        t.append(inner[:ready_at], style=f"dim {_LABEL_GREY}")
        t.append(inner[ready_at:], style=_VALUE_BLUE)
      else:
        t.append(inner, style=_VALUE_BLUE)
    elif stripped.startswith(("Provider", "Model", "Endpoint")):
      t.append(inner[:1])
      t.append(inner[1:11], style=f"dim {_LABEL_GREY}")
      t.append(inner[11:], style=_VALUE_BLUE)
    else:
      t.append(inner, style=_VALUE_BLUE)
    t.append("│", style=f"dim {_BORDER_GREY}")
    t.append(line[last + 1 :])
    return t

  i = 0
  while i < len(line) and line[i] == " ":
    t.append(line[i])
    i += 1
  while i < len(line):
    c = line[i]
    if c in _BOX_DRAW:
      t.append(c, style=f"dim {_BORDER_GREY}")
    else:
      t.append(c)
    i += 1
  return t


def print_welcome_dashboard(cfg, *, console: Console) -> None:
  from rich.console import Group
  from rich.text import Text

  tw = max(60, console.width or 80)
  raw = format_welcome_banner(cfg, term_width=tw)
  lines = raw.splitlines()
  art_total = max(1, sum(1 for x in lines if "█" in x))
  art_i = 0
  rows: list[Text] = []
  for ln in lines:
    low = ln.lower()
    if "█" in ln:
      line_t = art_i / max(art_total - 1, 1)
      rows.append(_gradient_logo_line(ln, line_t=line_t))
      art_i += 1
    elif any(c in ln for c in "╔╗║╚╠╣═│") and "█" not in ln:
      rows.append(_info_box_line(ln))
    elif "gemcode v" in low:
      rows.append(Text(ln, style=f"bold {_BLUE_MAIN}"))
    elif "✦" in ln:
      rows.append(Text(ln, style=f"dim italic {_LABEL_GREY}"))
    elif ln.strip().startswith("Tip:"):
      rows.append(Text(ln, style=f"dim {_LABEL_GREY}"))
    else:
      rows.append(Text(ln))
  console.print(Group(*rows))


def print_shortcuts_hint(*, console: Console) -> None:
  from rich.text import Text

  console.print(
    Text(
      "  Type a message to begin  ·  / for commands  ·  ↑↓ history",
      style=f"dim {_LABEL_GREY}",
    )
  )
