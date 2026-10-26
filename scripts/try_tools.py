"""Runs each tool by hand on a few companies so we can see its raw output (no LLM, no Groq tokens).

Usage: python -m scripts.try_tools "Acme Ltd|https://acme.com" "Other Co|https://other.co"
Uses exactly 2 Tavily searches per company; fetches and job boards don't touch Tavily.
"""

import sys
from urllib.parse import urlparse

from src.tools.fetch import fetch_page
from src.tools.jobs import find_jobs
from src.tools.search import SearchResponse, search_usage, web_search

JOB_BOARD_HOSTS = ("greenhouse.io", "lever.co")


def _host(url: str) -> str:
    """The URL's host without 'www.', for comparing domains."""
    return (urlparse(url).hostname or "").removeprefix("www.")


def _pick(response: SearchResponse, host: str, word: str, allow_job_boards: bool = False) -> str | None:
    """The first result on the company's host whose URL contains word, else the first on the host at all."""
    on_site = [r.url for r in response.results if _host(r.url).endswith(host)]
    if allow_job_boards:
        on_site += [r.url for r in response.results if _host(r.url).endswith(JOB_BOARD_HOSTS)]
    for url in on_site:
        if word in url.lower():
            return url
    if on_site:
        return on_site[0]
    return None


def _page_line(label: str, url: str | None) -> str:
    """Fetch a page and describe the result in one line."""
    if url is None:
        return f"  {label:8} not found in search results"
    page = fetch_page(url)
    state = "EMPTY/JS -> unknown" if page.empty_or_js else f"{len(page.text)} chars of text"
    return f"  {label:8} {page.status} {state} | {page.url}" + (f" | {page.error}" if page.error else "")


def try_company(company: str, website: str | None) -> None:
    """Run all three tools on one company and print a readable summary."""
    print(f"\n=== {company} ===")
    about_search = web_search(f"{company} about")
    careers_search = web_search(f"{company} careers")

    if website:
        host = _host(website)
        found = any(_host(r.url).endswith(host) for r in about_search.results + careers_search.results)
        print(f"  site     {'found' if found else 'NOT found'} in search: {host}  [{about_search.engine}]")
    else:
        host = _host(about_search.results[0].url) if about_search.results else ""
        print(f"  site     no website given; best guess from search: {host or 'none'}  [{about_search.engine}]")

    print(_page_line("about", _pick(about_search, host, "about") if host else None))
    careers_url = _pick(careers_search, host, "career", allow_job_boards=True) if host else None
    if careers_url is None and website:
        # Search ranking shouldn't decide whether we see the careers page: try the usual address
        careers_url = website.rstrip("/") + "/careers"
        print(f"  careers  not in search results, trying {careers_url} directly")
    print(_page_line("careers", careers_url))

    jobs = find_jobs(company, website)
    if jobs.board:
        print(f"  jobs     {jobs.total_roles} roles on {jobs.board}, {jobs.found_via} ({jobs.board_url})")
    else:
        print("  jobs     no Greenhouse/Lever board found -> hiring unknown")


def main() -> None:
    """Parse 'Company|website' arguments and try each company."""
    sys.stdout.reconfigure(encoding="utf-8")  # show page text correctly on Windows consoles
    if len(sys.argv) < 2:
        sys.exit('Usage: python -m scripts.try_tools "Company|https://website" ...')
    for argument in sys.argv[1:]:
        company, _, website = argument.partition("|")
        try_company(company.strip(), website.strip() or None)
    print(f"\nSearch usage this run: {search_usage()}")


if __name__ == "__main__":
    main()
