"""ASCII welcome banner + provider box (scrollback + full-screen TUI)."""

from __future__ import annotations

import os
from pathlib import Path

from gemcode.version import get_version
from gemcode.vertex import vertex_env_active
from gemcode.workspace_hints import narrow_workspace_tip

_GEM_LINES = (
    r"██████╗ ███████╗███╗   ███╗",
    r"██╔════╝ ██╔════╝████╗ ████║",
    r"██║  ███╗█████╗  ██╔████╔██║",
    r"██║   ██║██╔══╝  ██║╚██╔╝██║",
    r"╚██████╔╝███████╗██║ ╚═╝ ██║",
    r" ╚═════╝ ╚══════╝╚═╝     ╚═╝",
)

_CODE_LINES = (
    r"██████╗ ██████╗ ██████╗ ███████╗",
    r"██╔════╝██╔═══██╗██╔══██╗██╔════╝",
    r"██║     ██║   ██║██║  ██║█████╗  ",
    r"██║     ██║   ██║██║  ██║██╔══╝  ",
    r"╚██████╗╚██████╔╝██████╔╝███████╗",
    r" ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝",
)

_TAGLINE = "✦ Gemini-powered coding agent. Fast. Capable. Local. ✦"


def _banner_width() -> int:
  return max(len(x) for x in _GEM_LINES + _CODE_LINES)


def _center(line: str, width: int) -> str:
  if len(line) >= width:
    return line
  pad = width - len(line)
  left = pad // 2
  return " " * left + line + " " * (pad - left)


def _provider_model_endpoint(cfg) -> tuple[str, str, str]:
  model = (getattr(cfg, "model", None) or "").strip() or "gemini-2.5-flash"
  if vertex_env_active():
    provider = "Vertex AI"
    loc = (os.environ.get("GOOGLE_CLOUD_LOCATION") or "us-central1").strip()
    proj = (os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    end = f"vertex://{loc}"
    if proj:
      end = f"{end} · {proj}"
  else:
    provider = "Google Gemini"
    end = "https://generativelanguage.googleapis.com/"
  return provider, model, end


def _kv_line(inner: int, label: str, value: str) -> str:
  """One row inside the box: leading space + label column + value (total width ``inner``)."""
  lead = " "
  prefix = f"{label:<10}"
  room = inner - len(lead) - len(prefix)
  if room < 4:
    raw = (lead + prefix + value)[:inner]
    return raw.ljust(inner)
  v = value
  if len(v) > room:
    v = v[: max(0, room - 3)] + "..."
  return (lead + prefix + v).ljust(inner)[:inner]


def format_welcome_banner(cfg, *, term_width: int = 100) -> str:
  provider, model, endpoint = _provider_model_endpoint(cfg)
  # Banner style: "solid" removes the boxed provider block and blank spacer
  # lines for a single rigid header (OpenClaude-like).
  style = (os.environ.get("GEMCODE_TUI_BANNER_STYLE", "solid") or "solid").strip().lower()

  bw = max(_banner_width(), min(80, max(48, term_width)))
  lines: list[str] = []
  for row in _GEM_LINES:
    lines.append(_center(row, bw))
  for row in _CODE_LINES:
    lines.append(_center(row, bw))
  lines.append(_center(_TAGLINE, bw))

  if style not in ("solid", "rigid", "compact"):
    # Backward-compat: unknown values fall back to the old boxed layout.
    style = "boxed"

  if style == "boxed":
    inner = min(60, max(48, term_width - 4))
    top = "╔" + ("═" * inner) + "╗"
    mid = "╠" + ("═" * inner) + "╣"
    bot = "╚" + ("═" * inner) + "╝"

    row_p = _kv_line(inner, "Provider", provider)
    row_m = _kv_line(inner, "Model", model)
    row_e = _kv_line(inner, "Endpoint", endpoint)

    mode = "vertex" if vertex_env_active() else "cloud"
    status_raw = f" ● {mode}    Ready — type /help to begin"
    if len(status_raw) > inner:
      status_raw = status_raw[: max(0, inner - 3)] + "..."
    row_s = status_raw.ljust(inner)[:inner]

    box_w = inner + 2
    pad_b = max(0, (bw - box_w) // 2)
    bp = " " * pad_b

    def box_row(body: str) -> str:
      b = body[:inner].ljust(inner)
      return bp + "│" + b + "│"

    lines.append(bp + top)
    lines.append(box_row(row_p))
    lines.append(box_row(row_m))
    lines.append(box_row(row_e))
    lines.append(bp + mid)
    lines.append(box_row(row_s))
    lines.append(bp + bot)
  else:
    # Solid header: one rigid info line, no extra blocks.
    mode = "vertex" if vertex_env_active() else "cloud"
    info = f"{provider} · {model} · {endpoint} · {mode} ready — type /help"
    lines.append(_center(info, bw))

  ver = os.environ.get("GEMCODE_VERSION", get_version())
  lines.append(_center(f"gemcode v{ver}", bw))

  root = getattr(cfg, "project_root", None)
  if isinstance(root, Path):
    nt = narrow_workspace_tip(root)
    if nt:
      lines.append(_center(nt, bw))
  return "\n".join(lines)
