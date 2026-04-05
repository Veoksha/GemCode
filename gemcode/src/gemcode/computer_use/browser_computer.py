"""
Playwright-backed browser automation (ADK ComputerUse).

Implements Google ADK's BaseComputer so the LLM can call computer-use tools
like navigate, click_at, type_text_at, scroll_at, key_combination, etc.

Every action returns a ComputerState (viewport PNG screenshot + current URL)
so the model always sees the current page state after each action.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from typing import Literal
from typing import Tuple

from google.adk.tools.computer_use.base_computer import BaseComputer
from google.adk.tools.computer_use.base_computer import ComputerEnvironment
from google.adk.tools.computer_use.base_computer import ComputerState


@dataclass(frozen=True)
class BrowserComputerConfig:
  headless: bool = True
  viewport_width: int = 1280
  viewport_height: int = 720
  navigation_timeout_ms: int = 30_000
  # Extra ms to wait after load for dynamic content (SPAs, lazy-loading, etc.)
  post_load_wait_ms: int = 400


_IS_MACOS = platform.system() == "Darwin"

# On macOS, many "select all / copy / paste" shortcuts use Cmd (Meta) instead
# of Ctrl.  In a Chromium browser Ctrl+A still works for web inputs, but some
# OS-level UI elements need Meta.  We expose both and let the agent decide.
_MOD_MAP = {
    "control": "Control", "ctrl": "Control",
    "shift": "Shift",
    "alt": "Alt", "option": "Alt",
    "meta": "Meta", "command": "Meta", "cmd": "Meta", "super": "Meta",
}


class BrowserComputer(BaseComputer):
  """
  A single Chromium page controlled by Playwright.

  Usage flow:
    1. LLM calls ``navigate(url)`` to open a page.
    2. LLM reads the screenshot returned by each action.
    3. LLM calls ``click_at``, ``type_text_at``, etc. to interact.
    4. Each action returns a new ``ComputerState`` (screenshot + URL).
  """

  def __init__(
    self,
    *,
    headless: bool = True,
    viewport_size: Tuple[int, int] = (1280, 720),
    navigation_timeout_ms: int = 30_000,
    post_load_wait_ms: int = 400,
  ):
    self._cfg = BrowserComputerConfig(
      headless=headless,
      viewport_width=viewport_size[0],
      viewport_height=viewport_size[1],
      navigation_timeout_ms=navigation_timeout_ms,
      post_load_wait_ms=post_load_wait_ms,
    )
    self._playwright = None
    self._browser = None
    self._context = None
    self._page = None

  # ── Lifecycle ─────────────────────────────────────────────────────────────

  async def initialize(self) -> None:
    """Lazy-start Playwright Chromium. Called automatically before every action."""
    if self._page is not None:
      return
    try:
      from playwright.async_api import async_playwright
    except ImportError as exc:
      raise RuntimeError(
        "Browser computer requires Playwright.\n"
        "  pip install playwright\n"
        "  playwright install chromium"
      ) from exc

    self._playwright = await async_playwright().start()
    self._browser = await self._playwright.chromium.launch(headless=self._cfg.headless)
    self._context = await self._browser.new_context(
      viewport={
        "width": self._cfg.viewport_width,
        "height": self._cfg.viewport_height,
      }
    )
    self._page = await self._context.new_page()
    self._page.set_default_navigation_timeout(self._cfg.navigation_timeout_ms)

  async def close(self) -> None:
    """Shut down the Playwright process (called by session_runtime on runner close)."""
    for attr in ("_context", "_browser", "_playwright"):
      obj = getattr(self, attr, None)
      if obj is not None:
        try:
          await obj.close() if attr != "_playwright" else await obj.stop()
        except Exception:
          pass
    self._playwright = None
    self._browser = None
    self._context = None
    self._page = None

  # ── ADK BaseComputer interface ─────────────────────────────────────────────

  async def environment(self) -> ComputerEnvironment:
    return ComputerEnvironment.ENVIRONMENT_BROWSER

  async def screen_size(self) -> tuple[int, int]:
    await self.initialize()
    return (self._cfg.viewport_width, self._cfg.viewport_height)

  async def current_state(self) -> ComputerState:
    """Return viewport PNG + current URL (called after every action)."""
    await self.initialize()
    screenshot = await self._page.screenshot(type="png", full_page=False)
    url = self._page.url
    return ComputerState(screenshot=screenshot, url=url)

  # ── Navigation ─────────────────────────────────────────────────────────────

  async def open_web_browser(self) -> ComputerState:
    await self.initialize()
    await self._page.goto("about:blank")
    return await self.current_state()

  async def navigate(self, url: str) -> ComputerState:
    """Navigate to a URL. Waits for DOM content, then an extra grace period."""
    await self.initialize()
    try:
      await self._page.goto(url, wait_until="domcontentloaded")
    except Exception:
      # Best-effort on timeout/error; still return current state.
      pass
    await self._page.wait_for_timeout(self._cfg.post_load_wait_ms)
    return await self.current_state()

  async def go_back(self) -> ComputerState:
    await self.initialize()
    try:
      await self._page.go_back(wait_until="domcontentloaded")
    except Exception:
      pass
    await self._page.wait_for_timeout(250)
    return await self.current_state()

  async def go_forward(self) -> ComputerState:
    await self.initialize()
    try:
      await self._page.go_forward(wait_until="domcontentloaded")
    except Exception:
      pass
    await self._page.wait_for_timeout(250)
    return await self.current_state()

  async def search(self) -> ComputerState:
    return await self.navigate("https://www.google.com")

  # ── Mouse actions ──────────────────────────────────────────────────────────

  async def click_at(self, x: int, y: int) -> ComputerState:
    """Left-click at pixel coordinates (0,0 = top-left)."""
    await self.initialize()
    await self._page.mouse.click(x, y)
    await self._page.wait_for_timeout(150)
    return await self.current_state()

  async def double_click_at(self, x: int, y: int) -> ComputerState:
    """Double-click at pixel coordinates."""
    await self.initialize()
    await self._page.mouse.dblclick(x, y)
    await self._page.wait_for_timeout(150)
    return await self.current_state()

  async def right_click_at(self, x: int, y: int) -> ComputerState:
    """Right-click (context menu) at pixel coordinates."""
    await self.initialize()
    await self._page.mouse.click(x, y, button="right")
    await self._page.wait_for_timeout(150)
    return await self.current_state()

  async def hover_at(self, x: int, y: int) -> ComputerState:
    """Hover mouse (reveals tooltips and dropdowns)."""
    await self.initialize()
    await self._page.mouse.move(x, y)
    await self._page.wait_for_timeout(200)
    return await self.current_state()

  async def drag_and_drop(
    self, x: int, y: int, destination_x: int, destination_y: int
  ) -> ComputerState:
    await self.initialize()
    await self._page.mouse.move(x, y)
    await self._page.mouse.down()
    await self._page.mouse.move(destination_x, destination_y, steps=10)
    await self._page.mouse.up()
    await self._page.wait_for_timeout(250)
    return await self.current_state()

  # ── Scroll ─────────────────────────────────────────────────────────────────

  async def scroll_document(
    self, direction: Literal["up", "down", "left", "right"]
  ) -> ComputerState:
    """Scroll the whole page by a fixed amount."""
    await self.initialize()
    dx, dy = self._direction_delta(direction, magnitude=700)
    await self._page.mouse.wheel(dx=dx, dy=dy)
    await self._page.wait_for_timeout(250)
    return await self.current_state()

  async def scroll_at(
    self,
    x: int,
    y: int,
    direction: Literal["up", "down", "left", "right"],
    magnitude: int,
  ) -> ComputerState:
    """Scroll at a specific (x, y) coordinate (for scrollable panels)."""
    await self.initialize()
    await self._page.mouse.move(x, y)
    dx, dy = self._direction_delta(direction, magnitude=abs(magnitude))
    await self._page.mouse.wheel(dx=dx, dy=dy)
    await self._page.wait_for_timeout(250)
    return await self.current_state()

  @staticmethod
  def _direction_delta(
    direction: str, magnitude: int
  ) -> tuple[int, int]:
    if direction == "up":
      return 0, -magnitude
    if direction == "down":
      return 0, magnitude
    if direction == "left":
      return -magnitude, 0
    return magnitude, 0

  # ── Keyboard ───────────────────────────────────────────────────────────────

  async def type_text_at(
    self,
    x: int,
    y: int,
    text: str,
    press_enter: bool = True,
    clear_before_typing: bool = True,
  ) -> ComputerState:
    """
    Click a field, optionally clear it, then type text.

    On macOS browsers Ctrl+A selects all text in web inputs correctly.
    For native OS dialogs use key_combination(["Meta+a"]) instead.
    """
    await self.initialize()
    await self._page.mouse.click(x, y)
    await self._page.wait_for_timeout(100)
    if clear_before_typing:
      select_all = "Meta+A" if _IS_MACOS else "Control+A"
      await self._page.keyboard.press(select_all)
      await self._page.keyboard.press("Backspace")
    await self._page.keyboard.type(text, delay=30)
    if press_enter:
      await self._page.keyboard.press("Enter")
    return await self.current_state()

  async def key_combination(self, keys: list[str]) -> ComputerState:
    """
    Press key combinations.

    Each element in ``keys`` is a combo string like ``"control+c"`` or
    ``"shift+enter"``.  Modifier names: control/ctrl, shift, alt/option,
    meta/command/cmd.

    Example: ``key_combination(["control+a", "control+c"])``
    """
    await self.initialize()
    for combo in keys:
      await self._press_combo(combo)
    await self._page.wait_for_timeout(100)
    return await self.current_state()

  async def _press_combo(self, combo: str) -> None:
    """Press a single key combination string (e.g. 'control+shift+t')."""
    parts = combo.strip().lower().replace(" ", "").split("+")
    if len(parts) == 1:
      await self._page.keyboard.press(parts[0])
      return
    mods = parts[:-1]
    key = parts[-1]
    for m in mods:
      pw = _MOD_MAP.get(m)
      if pw:
        await self._page.keyboard.down(pw)
    await self._page.keyboard.press(key)
    for m in reversed(mods):
      pw = _MOD_MAP.get(m)
      if pw:
        await self._page.keyboard.up(pw)

  # ── Waiting ────────────────────────────────────────────────────────────────

  async def wait(self, seconds: int) -> ComputerState:
    """Wait N seconds (use when page is loading dynamic content)."""
    await self.initialize()
    await self._page.wait_for_timeout(max(1, int(seconds)) * 1000)
    return await self.current_state()

  async def wait_for_text(self, text: str, timeout_seconds: int = 10) -> ComputerState:
    """Wait until a specific text appears on the page."""
    await self.initialize()
    try:
      await self._page.wait_for_function(
        f"document.body.innerText.includes({repr(text)})",
        timeout=timeout_seconds * 1000,
      )
    except Exception:
      pass
    return await self.current_state()

  # ── Page inspection ────────────────────────────────────────────────────────

  async def get_page_text(self) -> str:
    """Extract all visible text from the current page (for data extraction)."""
    await self.initialize()
    try:
      return await self._page.evaluate("() => document.body.innerText")
    except Exception as e:
      return f"(error reading page text: {e})"

  async def get_page_title(self) -> str:
    """Return the current page <title>."""
    await self.initialize()
    try:
      return await self._page.title()
    except Exception:
      return ""

  async def get_current_url(self) -> str:
    """Return the current page URL."""
    await self.initialize()
    return self._page.url or ""

  async def screenshot_bytes(self) -> bytes:
    """Return the current viewport as a PNG bytes object (no action)."""
    await self.initialize()
    return await self._page.screenshot(type="png", full_page=False)
