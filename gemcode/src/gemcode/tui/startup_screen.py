"""
GemCode startup screen with gradient ASCII art logo.
Inspired by OpenClaude's beautiful startup experience.
"""

from __future__ import annotations

import os
import sys
from typing import NamedTuple


class RGB(NamedTuple):
    r: int
    g: int
    b: int


ESC = "\x1b["
RESET = f"{ESC}0m"
DIM = f"{ESC}2m"


def rgb(r: int, g: int, b: int) -> str:
    return f"{ESC}38;2;{r};{g};{b}m"


def lerp(a: RGB, b: RGB, t: float) -> RGB:
    return RGB(
        round(a.r + (b.r - a.r) * t),
        round(a.g + (b.g - a.g) * t),
        round(a.b + (b.b - a.b) * t),
    )


def grad_at(stops: list[RGB], t: float) -> RGB:
    c = max(0.0, min(1.0, t))
    s = c * (len(stops) - 1)
    i = int(s)
    if i >= len(stops) - 1:
        return stops[-1]
    return lerp(stops[i], stops[i + 1], s - i)


def paint_line(text: str, stops: list[RGB], line_t: float) -> str:
    out = ""
    for i, ch in enumerate(text):
        t = line_t * 0.5 + (i / (len(text) - 1)) * 0.5 if len(text) > 1 else line_t
        color = grad_at(stops, t)
        out += f"{rgb(color.r, color.g, color.b)}{ch}"
    return out + RESET


# Color palette - sunset gradient
SUNSET_GRAD: list[RGB] = [
    RGB(255, 180, 100),
    RGB(240, 140, 80),
    RGB(217, 119, 87),
    RGB(193, 95, 60),
    RGB(160, 75, 55),
    RGB(130, 60, 50),
]

ACCENT = RGB(240, 148, 100)
CREAM = RGB(220, 195, 170)
DIMCOL = RGB(120, 100, 82)
BORDER = RGB(100, 80, 65)

# Filled block text logo
LOGO_GEM = [
    "  ██████╗ ███████╗███╗   ███╗",
    " ██╔════╝ ██╔════╝████╗ ████║",
    " ██║  ███╗█████╗  ██╔████╔██║",
    " ██║   ██║██╔══╝  ██║╚██╔╝██║",
    " ╚██████╔╝███████╗██║ ╚═╝ ██║",
    "  ╚═════╝ ╚══════╝╚═╝     ╚═╝",
]

LOGO_CODE = [
    "  ██████╗ ██████╗ ██████╗ ███████╗",
    " ██╔════╝██╔═══██╗██╔══██╗██╔════╝",
    " ██║     ██║   ██║██║  ██║█████╗  ",
    " ██║     ██║   ██║██║  ██║██╔══╝  ",
    " ╚██████╗╚██████╔╝██████╔╝███████╗",
    "  ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝",
]


def detect_provider() -> dict[str, str | bool]:
    """Detect current provider configuration."""
    # Check for Gemini (default for GemCode)
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    model = os.environ.get("GEMINI_MODEL") or os.environ.get("GEMCODE_MODEL") or "gemini-2.0-flash-exp"
    base_url = os.environ.get("GEMINI_BASE_URL") or "https://generativelanguage.googleapis.com"
    
    return {
        "name": "Google Gemini",
        "model": model,
        "base_url": base_url,
        "is_local": False,
        "has_key": bool(api_key),
    }


def box_row(content: str, width: int, raw_len: int) -> str:
    """Create a box row with proper padding."""
    pad = max(0, width - 2 - raw_len)
    return f"{rgb(BORDER.r, BORDER.g, BORDER.b)}│{RESET}{content}{' ' * pad}{rgb(BORDER.r, BORDER.g, BORDER.b)}│{RESET}"


def print_startup_screen() -> None:
    """Print the beautiful gradient startup screen."""
    # Skip in non-interactive / CI environments
    if os.environ.get("CI") or not sys.stdout.isatty():
        return
    
    # Skip if explicitly disabled
    if os.environ.get("GEMCODE_NO_STARTUP_SCREEN", "").lower() in ("1", "true", "yes", "on"):
        return
    
    p = detect_provider()
    W = 62
    out: list[str] = []
    
    out.append("")
    
    # Gradient logo
    all_logo = LOGO_GEM + [""] + LOGO_CODE
    total = len(all_logo)
    for i, line in enumerate(all_logo):
        t = i / (total - 1) if total > 1 else 0
        if line == "":
            out.append("")
        else:
            out.append(paint_line(line, SUNSET_GRAD, t))
    
    out.append("")
    
    # Tagline
    out.append(
        f"  {rgb(ACCENT.r, ACCENT.g, ACCENT.b)}✦{RESET} "
        f"{rgb(CREAM.r, CREAM.g, CREAM.b)}Gemini-powered coding agent. Fast. Capable. Local.{RESET} "
        f"{rgb(ACCENT.r, ACCENT.g, ACCENT.b)}✦{RESET}"
    )
    out.append("")
    
    # Provider info box
    out.append(f"{rgb(BORDER.r, BORDER.g, BORDER.b)}╔{'═' * (W - 2)}╗{RESET}")
    
    def lbl(k: str, v: str, c: RGB = CREAM) -> tuple[str, int]:
        pad_k = k.ljust(9)
        return (
            f" {DIM}{rgb(DIMCOL.r, DIMCOL.g, DIMCOL.b)}{pad_k}{RESET} {rgb(c.r, c.g, c.b)}{v}{RESET}",
            len(f" {pad_k} {v}"),
        )
    
    prov_c = RGB(130, 175, 130) if p["is_local"] else ACCENT
    r, l = lbl("Provider", str(p["name"]), prov_c)
    out.append(box_row(r, W, l))
    
    r, l = lbl("Model", str(p["model"]))
    out.append(box_row(r, W, l))
    
    ep = str(p["base_url"])
    if len(ep) > 38:
        ep = ep[:35] + "..."
    r, l = lbl("Endpoint", ep)
    out.append(box_row(r, W, l))
    
    out.append(f"{rgb(BORDER.r, BORDER.g, BORDER.b)}╠{'═' * (W - 2)}╣{RESET}")
    
    # Status line
    s_c = RGB(130, 175, 130) if p["is_local"] else ACCENT
    s_l = "local" if p["is_local"] else "cloud"
    status_text = (
        f" {rgb(s_c.r, s_c.g, s_c.b)}●{RESET} "
        f"{DIM}{rgb(DIMCOL.r, DIMCOL.g, DIMCOL.b)}{s_l}{RESET}    "
        f"{DIM}{rgb(DIMCOL.r, DIMCOL.g, DIMCOL.b)}Ready — type {RESET}"
        f"{rgb(ACCENT.r, ACCENT.g, ACCENT.b)}/help{RESET}"
        f"{DIM}{rgb(DIMCOL.r, DIMCOL.g, DIMCOL.b)} to begin{RESET}"
    )
    s_len = len(f" ● {s_l}    Ready — type /help to begin")
    out.append(box_row(status_text, W, s_len))
    
    out.append(f"{rgb(BORDER.r, BORDER.g, BORDER.b)}╚{'═' * (W - 2)}╝{RESET}")
    
    # Version
    from gemcode.version import get_version
    version = os.environ.get("GEMCODE_VERSION", get_version())
    out.append(
        f"  {DIM}{rgb(DIMCOL.r, DIMCOL.g, DIMCOL.b)}gemcode {RESET}"
        f"{rgb(ACCENT.r, ACCENT.g, ACCENT.b)}v{version}{RESET}"
    )
    out.append("")
    
    print("\n".join(out), flush=True)
