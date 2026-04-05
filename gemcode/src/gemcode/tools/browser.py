"""
Standalone browser inspection tools (complement to ADK ComputerUseToolset).

These are plain async functions exposed to the LLM as regular GemCode tools.
They give the agent *read-only* views of the current browser state so it can:

  - Take a screenshot at any time without performing a side-effecting action
  - Read all visible page text for structured data extraction
  - Inspect the current URL and page title
  - Find element positions by CSS selector or visible text

Usage: injected into the agent's tool list by session_runtime.py when
``cfg.enable_computer_use`` is True (they close over the BrowserComputer
instance created there).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from gemcode.config import GemCodeConfig


def build_browser_inspection_tools(
  cfg: GemCodeConfig,
  computer: Any,  # BrowserComputer — typed as Any to avoid circular import
) -> list:
  """
  Return a list of async tool functions that wrap the active BrowserComputer.

  These are appended to the agent's tool list in session_runtime.py and give
  the agent read-only inspection access without going through ComputerUseToolset.
  """

  async def browser_screenshot() -> dict:
    """
    Take a screenshot of the current browser page and save it to a temp file.

    Returns the file path, current URL, and page title.
    Use this to inspect the current browser state without performing any action.
    Call this after navigation or whenever you need to see what the page looks like.
    """
    try:
      png = await computer.screenshot_bytes()
      # Save to a predictable temp location so the user can view it too.
      tmp_dir = Path(tempfile.gettempdir()) / "gemcode_browser"
      tmp_dir.mkdir(parents=True, exist_ok=True)
      # Use a rolling slot so we don't accumulate unlimited files.
      slot = getattr(cfg, "_browser_screenshot_slot", 0) % 10
      setattr(cfg, "_browser_screenshot_slot", slot + 1)
      path = tmp_dir / f"screenshot_{slot:02d}.png"
      path.write_bytes(png)
      url = await computer.get_current_url()
      title = await computer.get_page_title()
      return {
          "screenshot_path": str(path),
          "url": url,
          "title": title,
          "size_bytes": len(png),
          "viewport": f"{computer._cfg.viewport_width}×{computer._cfg.viewport_height}",
          "hint": (
              "Screenshot saved. Analyze its contents carefully: identify buttons, "
              "input fields, text, and their approximate pixel positions. "
              "Remember: coordinates are (x, y) from the top-left corner (0, 0)."
          ),
      }
    except Exception as e:
      return {"error": f"browser_screenshot failed: {type(e).__name__}: {e}"}

  async def browser_get_text(max_chars: int = 20_000) -> dict:
    """
    Extract all visible text content from the current browser page.

    More reliable than screenshot-based reading for structured data extraction,
    finding exact text on a page, or verifying page content.
    Returns the full visible text (truncated to max_chars) plus URL and title.
    """
    try:
      url = await computer.get_current_url()
      title = await computer.get_page_title()
      text = await computer.get_page_text()
      truncated = len(text) > max_chars
      return {
          "url": url,
          "title": title,
          "text": text[:max_chars],
          "length": len(text),
          "truncated": truncated,
      }
    except Exception as e:
      return {"error": f"browser_get_text failed: {type(e).__name__}: {e}"}

  async def browser_get_url() -> dict:
    """
    Return the current browser URL and page title.

    Use this to confirm which page the browser is on, or to verify navigation
    succeeded before interacting with elements.
    """
    try:
      url = await computer.get_current_url()
      title = await computer.get_page_title()
      return {"url": url, "title": title}
    except Exception as e:
      return {"error": f"browser_get_url failed: {type(e).__name__}: {e}"}

  async def browser_find_element(
    selector_or_text: str,
    selector_type: str = "text",
  ) -> dict:
    """
    Find an element on the page and return its bounding box (center x, y).

    Args:
      selector_or_text: CSS selector (e.g. '#submit-btn', '.login-form input')
                        or visible text to search for.
      selector_type: 'css' to use CSS selector, 'text' to find by visible text.

    Returns the center (x, y) pixel coordinates of the element, its bounding
    box, and the element's visible text.  Use these coordinates for click_at /
    type_text_at / hover_at calls.
    """
    try:
      await computer.initialize()
      page = computer._page
      if selector_type == "text":
        # Build a :has-text() selector for Playwright
        locator = page.get_by_text(selector_or_text, exact=False)
      else:
        locator = page.locator(selector_or_text)

      # Use first matching element
      el = locator.first
      bb = await el.bounding_box()
      if bb is None:
        return {
            "found": False,
            "selector": selector_or_text,
            "error": "Element not visible or has zero bounding box",
        }
      center_x = int(bb["x"] + bb["width"] / 2)
      center_y = int(bb["y"] + bb["height"] / 2)
      try:
        text = await el.inner_text()
      except Exception:
        text = ""
      return {
          "found": True,
          "selector": selector_or_text,
          "center_x": center_x,
          "center_y": center_y,
          "bounding_box": {
              "x": int(bb["x"]),
              "y": int(bb["y"]),
              "width": int(bb["width"]),
              "height": int(bb["height"]),
          },
          "text": text[:200] if text else "",
          "hint": f"Use click_at({center_x}, {center_y}) to interact with this element.",
      }
    except Exception as e:
      return {
          "found": False,
          "selector": selector_or_text,
          "error": f"browser_find_element failed: {type(e).__name__}: {e}",
      }

  async def browser_wait_for_navigation(timeout_seconds: int = 10) -> dict:
    """
    Wait for the current page to finish loading (after a click that triggers navigation).

    Call this after clicking a link or button that causes a page change, if the
    next action fails because the new page hasn't loaded yet.
    """
    try:
      await computer.initialize()
      page = computer._page
      await page.wait_for_load_state("domcontentloaded", timeout=timeout_seconds * 1000)
      await page.wait_for_timeout(300)
      url = await computer.get_current_url()
      title = await computer.get_page_title()
      return {"url": url, "title": title, "loaded": True}
    except Exception as e:
      return {"error": f"browser_wait_for_navigation: {type(e).__name__}: {e}", "loaded": False}

  tools = [
      browser_screenshot,
      browser_get_text,
      browser_get_url,
      browser_find_element,
      browser_wait_for_navigation,
  ]

  # Set __name__ for proper ADK tool registration
  for fn in tools:
    if not hasattr(fn, "__name__"):
      fn.__name__ = fn.__qualname__.split(".")[-1]

  return tools
