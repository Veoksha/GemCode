"""
Web fetch tool — retrieve URL content for research and documentation lookups.

Analogous to OpenClaude's WebFetchTool. Read-only; no special permissions needed.
Uses urllib (stdlib) so no extra dependencies.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    """Minimal HTML → plain text converter (strips tags, scripts, styles)."""

    def __init__(self):
        super().__init__()
        self._buf: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs):
        if tag in ("script", "style", "noscript", "head"):
            self._skip += 1

    def handle_endtag(self, tag: str):
        if tag in ("script", "style", "noscript", "head"):
            self._skip = max(0, self._skip - 1)
        if tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"):
            self._buf.append("\n")

    def handle_data(self, data: str):
        if self._skip == 0:
            self._buf.append(data)

    def text(self) -> str:
        raw = "".join(self._buf)
        # Collapse whitespace runs, preserve paragraph breaks
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _html_to_text(html: str) -> str:
    try:
        parser = _TextExtractor()
        parser.feed(html)
        return parser.text()
    except Exception:
        return html


def make_web_fetch_tool():
    def web_fetch(url: str, max_chars: int = 20_000, raw: bool = False) -> dict:
        """
        Fetch content from a URL and return it as text.

        Useful for:
        - Reading documentation: web_fetch("https://docs.python.org/3/library/pathlib.html")
        - Checking APIs: web_fetch("https://api.github.com/repos/owner/repo")
        - Researching packages: web_fetch("https://pypi.org/pypi/rich/json")
        - Reading READMEs, changelogs, or issue trackers online

        Set raw=True to get the raw HTML/JSON instead of extracted text.
        max_chars caps the returned content (default 20 000 chars).
        """
        if not url or not url.strip():
            return {"error": "url must not be empty"}
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            return {"error": "Only http:// and https:// URLs are supported"}
        if max_chars < 1000:
            max_chars = 1000
        if max_chars > 200_000:
            max_chars = 200_000

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; GemCode/1.0; +https://github.com/mohitanand/GemCode)"
                ),
                "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = resp.status
                content_type = resp.headers.get("Content-Type", "")
                raw_bytes = resp.read(500_000)
        except urllib.error.HTTPError as e:
            return {"error": f"HTTP {e.code}: {e.reason}", "url": url}
        except urllib.error.URLError as e:
            return {"error": f"URL error: {e.reason}", "url": url}
        except Exception as e:
            return {"error": f"Fetch failed: {e}", "url": url}

        charset = "utf-8"
        if "charset=" in content_type:
            try:
                charset = content_type.split("charset=")[-1].split(";")[0].strip()
            except Exception:
                charset = "utf-8"

        try:
            text = raw_bytes.decode(charset, errors="replace")
        except (LookupError, Exception):
            text = raw_bytes.decode("utf-8", errors="replace")

        is_html = "text/html" in content_type or text.lstrip().startswith("<")
        is_json = "json" in content_type

        if not raw and is_html:
            text = _html_to_text(text)
        elif is_json:
            pass  # return JSON as-is; useful for APIs

        truncated = len(text) > max_chars
        text = text[:max_chars]

        return {
            "url": url,
            "status": status,
            "content_type": content_type,
            "content": text,
            "truncated": truncated,
            "chars": len(text),
        }

    return web_fetch
