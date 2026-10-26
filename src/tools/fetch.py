"""fetch_page tool: downloads a URL and extracts readable text, with an optional page cache."""

import hashlib
import json
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

import httpx
import trafilatura

from src import config


@dataclass
class PageResult:
    """What fetch_page returns. A failed fetch has error set; it never raises."""

    url: str              # the final URL after redirects: where the text really came from
    status: int | None    # HTTP status, or None if no answer came back (timeout, DNS failure)
    text: str             # readable text, cut to max_chars
    empty_or_js: bool     # True: too little text to judge, so criteria stay "unknown"
    error: str | None


def is_blocked(url: str) -> bool:
    """True if the URL is on config.BLOCKED_DOMAINS (people data or review sites)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    for entry in config.BLOCKED_DOMAINS:
        domain, _, path = entry.partition("/")
        # "www.linkedin.com" and "in.linkedin.com" match; "notlinkedin.com" does not
        if (host == domain or host.endswith("." + domain)) and parsed.path.startswith("/" + path):
            return True
    return False


def _cache_path(url: str):
    """The cache file for a URL: its SHA-256 hash is always a safe, fixed-length file name."""
    return config.CACHE_DIR / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.json"


def _download(url: str) -> PageResult:
    """Fetch and extract one page live. Out: a PageResult holding the FULL text, not yet cut."""
    try:
        response = httpx.get(url, headers={"User-Agent": config.FETCH_USER_AGENT},
                             timeout=config.FETCH_TIMEOUT_SECONDS, follow_redirects=True)
    except httpx.HTTPError as error:
        # Timeouts, DNS failures, refused connections: there is no response at all
        return PageResult(url, None, "", True, f"{type(error).__name__}: {error}")

    final_url = str(response.url)
    if is_blocked(final_url):
        # The page redirected somewhere off-limits: throw the body away unread
        return PageResult(final_url, response.status_code, "", True, "blocked domain after redirect")
    if response.status_code != 200:
        # 403, 404, 429...: never retried; a blocked or missing page means "unknown"
        return PageResult(final_url, response.status_code, "", True, f"HTTP {response.status_code}")
    if "html" not in response.headers.get("content-type", "html"):
        return PageResult(final_url, 200, "", True, f"not an HTML page: {response.headers['content-type']}")

    # Bytes, not response.text: trafilatura detects the encoding from the page itself
    text = trafilatura.extract(response.content, include_comments=False, favor_recall=True) or ""
    return PageResult(final_url, 200, text, len(text) < config.MIN_PAGE_TEXT_CHARS, None)


def fetch_page(url: str, use_cache: bool = True,
               max_chars: int | None = config.MAX_PAGE_CHARS) -> PageResult:
    """Fetch a page's readable text.

    In: url; use_cache=False reads AND writes no cache (the checker must see the live page);
    max_chars=None returns the full text (the checker searches the whole page for a quote).
    Out: PageResult. Never raises.
    """
    if is_blocked(url):
        return PageResult(url, None, "", True, "blocked domain (ground rule: no personal data or review sites)")

    page = None
    cache_file = _cache_path(url)
    if use_cache and cache_file.exists():
        page = PageResult(**json.loads(cache_file.read_text(encoding="utf-8")))
    if page is None:
        page = _download(url)
        if use_cache and page.status == 200:
            # Only successes are cached, so a temporary outage isn't remembered forever
            config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(asdict(page)), encoding="utf-8")

    if max_chars is not None and len(page.text) > max_chars:
        page = PageResult(page.url, page.status, page.text[:max_chars], page.empty_or_js, page.error)
    return page


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "fetch_page",
        "description": ("Download one web page and return its readable text (cut to a fixed length). "
                        "If the page is empty, blocked or needs JavaScript, the result says so: "
                        "treat that as 'unknown', never as evidence that something is absent."),
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Full URL starting with https://"}},
            "required": ["url"],
        },
    },
}


def format_for_llm(page: PageResult) -> str:
    """Turn a PageResult into the text the LLM sees as the tool result."""
    header = f"url: {page.url}\nstatus: {page.status}"
    if page.error:
        return f"{header}\nerror: {page.error}\nNo usable text: treat this page as unknown."
    if page.empty_or_js:
        return f"{header}\nThe page had almost no readable text (JavaScript or a block page): treat as unknown."
    return f"{header}\ntext:\n{page.text}"
