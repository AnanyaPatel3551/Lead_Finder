"""web_search tool: queries Tavily (or ddgs as backup) and returns result titles, URLs and snippets."""

from dataclasses import dataclass, field

import httpx
from ddgs import DDGS

from src import config
from src.tools.fetch import is_blocked

TAVILY_URL = "https://api.tavily.com/search"

# Counted per process (one process = one run), so the run report can show search usage
_calls: dict[str, int] = {"tavily": 0, "ddgs": 0, "failed": 0}


@dataclass
class SearchResult:
    """One search hit."""

    title: str
    url: str
    snippet: str  # cut to MAX_SNIPPET_CHARS


@dataclass
class SearchResponse:
    """What web_search returns. engine is "tavily", "ddgs" or "none" (both failed). Never raises."""

    query: str
    engine: str
    results: list[SearchResult] = field(default_factory=list)
    error: str | None = None


def search_usage() -> dict[str, int]:
    """A copy of this run's search counts: successful calls per engine, and failures."""
    return dict(_calls)


def _tavily(query: str, max_results: int) -> list[SearchResult]:
    """Search with Tavily's REST API. Raises on any failure, so web_search can fall back."""
    if not config.TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY is empty")
    response = httpx.post(TAVILY_URL, timeout=config.FETCH_TIMEOUT_SECONDS,
                          headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"},
                          json={"query": query, "max_results": max_results, "search_depth": "basic"})
    response.raise_for_status()  # 401 bad key, 429 or 432 plan limit reached, ...
    hits = response.json().get("results", [])
    return [SearchResult(hit.get("title", ""), hit.get("url", ""),
                         (hit.get("content") or "")[:config.MAX_SNIPPET_CHARS]) for hit in hits]


def _ddgs(query: str, max_results: int) -> list[SearchResult]:
    """Search with DuckDuckGo through ddgs (no key). Raises on failure."""
    hits = DDGS().text(query, max_results=max_results)
    return [SearchResult(hit.get("title", ""), hit.get("href", ""),
                         (hit.get("body") or "")[:config.MAX_SNIPPET_CHARS]) for hit in hits]


def _allowed(results: list[SearchResult]) -> list[SearchResult]:
    """Drop results on blocked domains, so the LLM never even sees those URLs."""
    return [result for result in results if not is_blocked(result.url)]


def web_search(query: str, max_results: int = config.MAX_SEARCH_RESULTS) -> SearchResponse:
    """Search the web. Tavily first; on any Tavily failure, ddgs. Never raises.

    In: a query; max_results is capped at config.MAX_SEARCH_RESULTS to protect the token budget.
    """
    max_results = min(max_results, config.MAX_SEARCH_RESULTS)
    try:
        results = _allowed(_tavily(query, max_results))
        _calls["tavily"] += 1
        return SearchResponse(query, "tavily", results)
    except Exception as tavily_error:  # any Tavily problem (no key, quota, network) -> fall back
        tavily_message = f"{type(tavily_error).__name__}: {tavily_error}"

    try:
        results = _allowed(_ddgs(query, max_results))
        _calls["ddgs"] += 1
        return SearchResponse(query, "ddgs", results, error=f"tavily failed ({tavily_message})")
    except Exception as ddgs_error:  # ddgs raises its own varied exceptions
        _calls["failed"] += 1
        return SearchResponse(query, "none", [],
                              error=f"tavily: {tavily_message} | ddgs: {type(ddgs_error).__name__}: {ddgs_error}")


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web. Returns up to 5 results, each with a title, URL and short snippet.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The search query"}},
            "required": ["query"],
        },
    },
}


def format_for_llm(response: SearchResponse) -> str:
    """Turn a SearchResponse into the text the LLM sees as the tool result."""
    if not response.results:
        return f"No results for '{response.query}'." + (f" ({response.error})" if response.engine == "none" else "")
    lines = []
    for number, result in enumerate(response.results, start=1):
        lines.append(f"{number}. {result.title}\n   {result.url}\n   {result.snippet}")
    return "\n".join(lines)
