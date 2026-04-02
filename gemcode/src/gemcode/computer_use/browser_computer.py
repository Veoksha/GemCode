"""
Playwright-backed browser automation (ADK ComputerUse).

This provides an ADK `BaseComputer` implementation so the LLM can call
computer-use tools like `click_at`, `type_text_at`, and `navigate`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from typing import Optional
from typing import Sequence
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


class BrowserComputer(BaseComputer):
  def __init__(
    self,
    *,
    headless: bool = True,
    viewport_size: Tuple[int, int] = (1280, 720),
    navigation_timeout_ms: int = 30_000,
  ):
    self._cfg = BrowserComputerConfig(
      headless=headless,
      viewport_width=viewport_size[0],
      viewport_height=viewport_size[1],
      navigation_timeout_ms=navigation_timeout_ms,
    )
    self._playwright = None
    self._browser = None
    self._context = None
    self._page = None

  async def initialize(self) -> None:
    if self._page is not None:
      return
    try:
      from playwright.async_api import async_playwright
    except ImportError as e:
      raise RuntimeError(
        "Browser computer requires Playwright. Install `playwright` and run `playwright install`."
      ) from e

    self._playwright = await async_playwright().start()
    self._browser = await self._playwright.chromium.launch(headless=self._cfg.headless)
    self._context = await self._browser.new_context(
      viewport={"width": self._cfg.viewport_width, "height": self._cfg.viewport_height}
    )
    self._page = await self._context.new_page()
    self._page.set_default_navigation_timeout(self._cfg.navigation_timeout_ms)

  async def close(self) -> None:
    if self._page is not None:
      try:
        await self._context.close()
      except Exception:
        pass
    if self._browser is not None:
      try:
        await self._browser.close()
      except Exception:
        pass
    if self._playwright is not None:
      try:
        await self._playwright.stop()
      except Exception:
        pass
    self._playwright = None
    self._browser = None
    self._context = None
    self._page = None

  async def environment(self) -> ComputerEnvironment:
    return ComputerEnvironment.ENVIRONMENT_BROWSER

  async def screen_size(self) -> tuple[int, int]:
    await self.initialize()
    return (self._cfg.viewport_width, self._cfg.viewport_height)

  async def current_state(self) -> ComputerState:
    await self.initialize()
    assert self._page is not None
    screenshot = await self._page.screenshot(type="png", full_page=False)
    url = self._page.url
    return ComputerState(screenshot=screenshot, url=url)

  async def open_web_browser(self) -> ComputerState:
    await self.initialize()
    assert self._page is not None
    # Use about:blank to establish a page; LLM can navigate afterwards.
    await self._page.goto("about:blank")
    return await self.current_state()

  async def click_at(self, x: int, y: int) -> ComputerState:
    await self.initialize()
    assert self._page is not None
    await self._page.mouse.click(x, y)
    return await self.current_state()

  async def hover_at(self, x: int, y: int) -> ComputerState:
    await self.initialize()
    assert self._page is not None
    await self._page.mouse.move(x, y)
    await self._page.wait_for_timeout(150)
    return await self.current_state()

  async def type_text_at(
    self,
    x: int,
    y: int,
    text: str,
    press_enter: bool = True,
    clear_before_typing: bool = True,
  ) -> ComputerState:
    await self.initialize()
    assert self._page is not None
    await self._page.mouse.click(x, y)
    if clear_before_typing:
      # MVP clear behavior: select all + backspace.
      await self._page.keyboard.press("Control+A")
      await self._page.keyboard.press("Backspace")
    await self._page.keyboard.type(text)
    if press_enter:
      await self._page.keyboard.press("Enter")
    return await self.current_state()

  async def scroll_document(
    self, direction: Literal["up", "down", "left", "right"]
  ) -> ComputerState:
    await self.initialize()
    assert self._page is not None
    magnitude = 600
    if direction == "up":
      dx, dy = 0, -magnitude
    elif direction == "down":
      dx, dy = 0, magnitude
    elif direction == "left":
      dx, dy = -magnitude, 0
    else:
      dx, dy = magnitude, 0
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
    await self.initialize()
    assert self._page is not None
    await self._page.mouse.move(x, y)
    if direction == "up":
      dx, dy = 0, -abs(magnitude)
    elif direction == "down":
      dx, dy = 0, abs(magnitude)
    elif direction == "left":
      dx, dy = -abs(magnitude), 0
    else:
      dx, dy = abs(magnitude), 0
    await self._page.mouse.wheel(dx=dx, dy=dy)
    await self._page.wait_for_timeout(250)
    return await self.current_state()

  async def wait(self, seconds: int) -> ComputerState:
    await self.initialize()
    assert self._page is not None
    await self._page.wait_for_timeout(max(1, int(seconds)) * 1000)
    return await self.current_state()

  async def go_back(self) -> ComputerState:
    await self.initialize()
    assert self._page is not None
    await self._page.go_back()
    await self._page.wait_for_timeout(250)
    return await self.current_state()

  async def go_forward(self) -> ComputerState:
    await self.initialize()
    assert self._page is not None
    await self._page.go_forward()
    await self._page.wait_for_timeout(250)
    return await self.current_state()

  async def search(self) -> ComputerState:
    # Home page for web search.
    return await self.navigate("https://www.google.com")

  async def navigate(self, url: str) -> ComputerState:
    await self.initialize()
    assert self._page is not None
    await self._page.goto(url, wait_until="load")
    await self._page.wait_for_timeout(250)
    return await self.current_state()

  async def key_combination(self, keys: list[str]) -> ComputerState:
    """
    Press a key combination.

    ADK can pass values like `["control+c"]`. We parse `control+c` into a
    Playwright modifier sequence.
    """
    await self.initialize()
    assert self._page is not None

    mod_map = {
      "control": "Control",
      "ctrl": "Control",
      "shift": "Shift",
      "alt": "Alt",
      "option": "Alt",
      "meta": "Meta",
      "command": "Meta",
      "cmd": "Meta",
      "super": "Meta",
    }

    def _press_key_combo(combo: str) -> None:
      parts = combo.lower().replace(" ", "").split("+")
      if len(parts) == 1:
        return self._page.keyboard.press(parts[0])
      mods = parts[:-1]
      key = parts[-1]
      # Playwright: down(mod) + press(key) + up(mod)
      async def _run():
        for m in mods:
          pw = mod_map.get(m, None)
          if pw:
            await self._page.keyboard.down(pw)
        await self._page.keyboard.press(key)
        for m in reversed(mods):
          pw = mod_map.get(m, None)
          if pw:
            await self._page.keyboard.up(pw)

      return _run()

    for k in keys:
      res = _press_key_combo(k)
      if hasattr(res, "__await__"):
        await res
      else:
        # `press` already awaited by Playwright; noop.
        pass

    return await self.current_state()

  async def drag_and_drop(
    self, x: int, y: int, destination_x: int, destination_y: int
  ) -> ComputerState:
    await self.initialize()
    assert self._page is not None
    await self._page.mouse.move(x, y)
    await self._page.mouse.down()
    await self._page.mouse.move(destination_x, destination_y)
    await self._page.mouse.up()
    await self._page.wait_for_timeout(250)
    return await self.current_state()

