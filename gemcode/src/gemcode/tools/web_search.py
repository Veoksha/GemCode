"""
Standalone web search tool — always available, no API key required.

Uses DuckDuckGo's HTML interface as the primary backend (no auth needed).
Falls back to the `duckduckgo_search` package if installed for richer results.

Analogous to Reference UI WebSearchTool. Use this for quick lookups; for
deep, multi-page research use /research on (which enables Google Search
grounding via the Gemini API).
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from gemcode.query_sanitizer import sanitize_tool_query


class _DDGResultParser(HTMLParser):
    """Parse DuckDuckGo HTML search results page into a list of hits."""

    def __init__(self):
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture_title = False
        self._capture_snippet = False
        self._capture_url = False
        self._depth = 0

    def handle_starttag(self, tag: str, attrs_list):
        attrs = dict(attrs_list)
        cls = attrs.get("class", "")

        # Result container
        if tag == "div" and "result__body" in cls:
            self._current = {"title": "", "url": "", "snippet": ""}
            return

        if self._current is None:
            return

        # Title link
        if tag == "a" and "result__a" in cls:
            href = attrs.get("href", "")
            if href.startswith("//duckduckgo.com/l/?uddg="):
                # DDG redirect — extract real URL
                try:
                    qs = urllib.parse.urlparse("https:" + href).query
                    params = urllib.parse.parse_qs(qs)
                    real = params.get("uddg", [""])[0]
                    if real:
                        self._current["url"] = urllib.parse.unquote(real)
                except Exception:
                    self._current["url"] = href
            elif href.startswith("http"):
                self._current["url"] = href
            self._capture_title = True
            return

        if tag == "a" and "result__snippet" in cls:
            self._capture_snippet = True
            return

        if tag == "span" and ("result__snippet" in cls or "snippet" in cls.lower()):
            self._capture_snippet = True

    def handle_endtag(self, tag: str):
        if tag == "a":
            self._capture_title = False
            self._capture_snippet = False
        if tag == "span":
            self._capture_snippet = False
        if tag == "div":
            if self._current and self._current.get("title") and self._current.get("url"):
                self.results.append(self._current)
                self._current = None

    def handle_data(self, data: str):
        if self._capture_title and self._current is not None:
            self._current["title"] += data
        elif self._capture_snippet and self._current is not None:
            self._current["snippet"] += data


def _make_ssl_context():
    """Build an SSL context that works on macOS without manual cert installation."""
    import ssl
    # Try system certs first (works on most Linux + macOS with certifi)
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
        return ctx
    except ImportError:
        pass
    # macOS: try the system keychain bundle
    try:
        import subprocess
        result = subprocess.run(
            ["security", "find-certificate", "-a", "-p", "/System/Library/Keychains/SystemRootCertificates.keychain"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.load_verify_locations(cadata=result.stdout)
            return ctx
    except Exception:
        pass
    # Last resort: use default context (may fail on some macOS setups)
    return ssl.create_default_context()


def _ddg_html_search(query: str, max_results: int = 8) -> list[dict[str, str]]:
    """Fetch and parse DuckDuckGo HTML search results."""
    import ssl
    params = urllib.parse.urlencode({
        "q": query,
        "kl": "us-en",  # locale
        "kp": "-1",     # safe search off
        "kav": "1",     # clean results
    })
    url = f"https://html.duckduckgo.com/html/?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    # Try with SSL verification; fall back to unverified on cert errors
    try:
        ctx = _make_ssl_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            html = resp.read(300_000).decode("utf-8", errors="replace")
    except ssl.SSLError:
        ctx = ssl._create_unverified_context()  # noqa: SLF001
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            html = resp.read(300_000).decode("utf-8", errors="replace")

    parser = _DDGResultParser()
    parser.feed(html)
    results = parser.results[:max_results]

    # Fallback: if parser found nothing, do a simpler regex extraction
    if not results:
        results = _ddg_regex_fallback(html, max_results)

    return results


def _ddg_regex_fallback(html: str, max_results: int) -> list[dict[str, str]]:
    """Simpler regex-based fallback when the HTML structure doesn't match."""
    results: list[dict[str, str]] = []
    # Match href links that look like real URLs (not DDG internal)
    link_re = re.compile(
        r'href="(https?://[^"]+)"[^>]*>\s*<[^>]+>([^<]{5,200})</[^>]+>',
        re.DOTALL,
    )
    seen: set[str] = set()
    for m in link_re.finditer(html):
        url = m.group(1)
        title = re.sub(r"\s+", " ", m.group(2)).strip()
        if "duckduckgo.com" in url or url in seen:
            continue
        seen.add(url)
        results.append({"title": title, "url": url, "snippet": ""})
        if len(results) >= max_results:
            break
    return results


def _try_duckduckgo_package(query: str, max_results: int) -> list[dict[str, str]] | None:
    """Try the duckduckgo_search package if installed (richer results)."""
    try:
        from duckduckgo_search import DDGS  # type: ignore[import]
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
        return results if results else None
    except ImportError:
        return None
    except Exception:
        return None


def make_web_search_tool():
    def web_search(
        query: str,
        max_results: int = 8,
    ) -> dict[str, Any]:
        """
        Search the web and return a list of results with title, URL, and snippet.

        Always available — no API key or research mode required.
        Uses DuckDuckGo (privacy-respecting, no tracking).

        Use when you need to:
        - Look up documentation, package versions, or error messages
        - Find recent news or releases about a library
        - Verify a fact or find examples
        - Discover GitHub repos, Stack Overflow answers, or blog posts

        For deep multi-page research (reading full articles, following links),
        pair with web_fetch() or enable /research on.

        Args:
            query: Search query string.
            max_results: Number of results to return (1–20, default 8).
        """
        if not query or not query.strip():
            return {"error": "query must not be empty"}
        raw = query.strip()
        s = sanitize_tool_query(raw)
        query = str(s.get("clean_query") or "").strip()
        if not query:
            return {"error": "query must not be empty"}
        max_results = max(1, min(int(max_results), 20))

        # Try the richer duckduckgo_search package first
        results = _try_duckduckgo_package(query, max_results)

        # Fall back to HTML scraping
        if not results:
            try:
                results = _ddg_html_search(query, max_results)
            except urllib.error.URLError as e:
                return {"error": f"Network error: {e.reason}", "query": query}
            except Exception as e:
                return {"error": f"Search failed: {type(e).__name__}: {e}", "query": query}

        return {
            "query": query,
            "query_sanitized": bool(s.get("was_sanitized")),
            "query_sanitizer_method": str(s.get("method")),
            "results": results,
            "count": len(results),
            "tip": "Use web_fetch(url) to read the full content of any result.",
        }

    return web_search
