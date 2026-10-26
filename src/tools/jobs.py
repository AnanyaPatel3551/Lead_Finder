"""find_jobs tool: finds a company's public job posts as evidence of its team and AI intent."""

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from src import config

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/departments"
LEVER_URL = "https://api.lever.co/v0/postings/{token}"
# Legal-form words carry no meaning in a board name ("Acme Ltd" -> board "acme")
LEGAL_WORDS = {"inc", "ltd", "llc", "plc", "pvt", "private", "limited", "corp", "corporation", "co", "gmbh"}

# Links to a board on the company's own careers page, e.g. job-boards.greenhouse.io/acme or jobs.lever.co/acme
BOARD_LINK_PATTERNS = [
    ("greenhouse", re.compile(r"greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)")),
]
NOT_BOARD_NAMES = {"v1", "embed", "boards", "jobs"}  # path words the patterns can catch by mistake
CAREERS_PATHS = ("/careers", "/jobs")


@dataclass
class JobRole:
    """One open role. Only these four fields: no recruiter or hiring-manager names (no personal data)."""

    title: str
    location: str
    team: str
    url: str


@dataclass
class JobsResult:
    """What find_jobs returns. No board found = board None and no roles; never raises."""

    company: str
    board: str | None = None        # "greenhouse" or "lever"
    board_url: str | None = None    # lets the agent check the board belongs to THIS company
    found_via: str | None = None    # linked from the company's own site (trustworthy) or guessed
    total_roles: int = 0            # before the MAX_JOB_ROLES cut
    roles: list[JobRole] = field(default_factory=list)


def boards_linked_from_site(website: str) -> list[tuple[str, str]]:
    """(board, name) pairs linked from the company's own /careers and /jobs pages. Never raises."""
    base = (website if "://" in website else "https://" + website).rstrip("/")
    found: list[tuple[str, str]] = []
    for path in CAREERS_PATHS:
        try:
            response = httpx.get(base + path, headers={"User-Agent": config.FETCH_USER_AGENT},
                                 timeout=config.FETCH_TIMEOUT_SECONDS, follow_redirects=True)
        except httpx.HTTPError:
            continue
        if response.status_code != 200:
            continue
        # Raw HTML, not extracted text: the board link is in an href that trafilatura would drop
        for board, pattern in BOARD_LINK_PATTERNS:
            for name in pattern.findall(response.text):
                if name.lower() not in NOT_BOARD_NAMES and (board, name) not in found:
                    found.append((board, name))
    return found


def board_guesses(company: str, website: str | None = None) -> list[str]:
    """Guess job-board names from the domain and the company name, best guess first."""
    guesses: list[str] = []
    if website:
        host = urlparse(website if "://" in website else "https://" + website).hostname or ""
        stem = host.removeprefix("www.").split(".")[0]
        guesses += [stem, stem.replace("-", "")]
    words = [w for w in re.findall(r"[a-z0-9]+", company.lower()) if w not in LEGAL_WORDS]
    if words:
        guesses += ["".join(words), "-".join(words)]
        if len(words[0]) >= 4:  # a short first word ("no", "the") would match strangers' boards
            guesses.append(words[0])
    unique = list(dict.fromkeys(g for g in guesses if g))  # dedupe, keep order
    return unique[:config.MAX_JOB_BOARD_GUESSES]


def _greenhouse(token: str) -> list[JobRole] | None:
    """Roles on a Greenhouse board, or None if the board doesn't exist."""
    response = httpx.get(GREENHOUSE_URL.format(token=token), timeout=config.FETCH_TIMEOUT_SECONDS)
    if response.status_code != 200:
        return None
    roles: dict[str, JobRole] = {}  # keyed by URL: a job can be listed under parent and child departments
    for department in response.json().get("departments", []):
        for job in department.get("jobs", []):
            location = (job.get("location") or {}).get("name", "")
            roles[job["absolute_url"]] = JobRole(job["title"].strip(), location, department["name"],
                                                 job["absolute_url"])
    return list(roles.values())


def _lever(token: str) -> list[JobRole] | None:
    """Roles on a Lever board, or None if the board doesn't exist."""
    response = httpx.get(LEVER_URL.format(token=token), params={"mode": "json"},
                         timeout=config.FETCH_TIMEOUT_SECONDS)
    if response.status_code != 200:
        return None
    roles = []
    for posting in response.json():
        categories = posting.get("categories") or {}
        team = categories.get("team") or categories.get("department") or ""
        roles.append(JobRole(posting.get("text", "").strip(), categories.get("location", ""), team,
                             posting["hostedUrl"]))
    return roles


def find_jobs(company: str, website: str | None = None, query: str | None = None) -> JobsResult:
    """Find open roles on the company's Greenhouse or Lever board.

    In: company name, optional website (better board guesses), optional query chosen by the
    caller (e.g. "machine learning") to keep only matching titles or teams.
    Out: JobsResult with at most MAX_JOB_ROLES roles. Never raises.
    """
    # Boards the company itself links to come first: no guessing, and almost certainly theirs
    candidates: list[tuple[str, str, str]] = []
    if website:
        for board, name in boards_linked_from_site(website):
            candidates.append((board, name, "linked from the company's own careers page"))
    for name in board_guesses(company, website):
        candidates.append(("greenhouse", name, "guessed from the company name"))
        candidates.append(("lever", name, "guessed from the company name"))

    for board, name, found_via in candidates:
        lookup = _greenhouse if board == "greenhouse" else _lever
        try:
            roles = lookup(name)
        except (httpx.HTTPError, ValueError, KeyError):  # network trouble or unexpected JSON
            roles = None
        if not roles:
            # Missing, or empty: an empty board is just as likely a stranger's, so keep looking
            continue
        if query:
            roles = [r for r in roles if query.lower() in f"{r.title} {r.team}".lower()]
        board_url = (GREENHOUSE_URL if board == "greenhouse" else LEVER_URL).format(token=name)
        return JobsResult(company, board=board, board_url=board_url, found_via=found_via,
                          total_roles=len(roles), roles=roles[:config.MAX_JOB_ROLES])
    return JobsResult(company)


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "find_jobs",
        "description": ("List the company's open roles from its public Greenhouse or Lever job board "
                        "(title, location, team, url). No board found does not mean no hiring: "
                        "the company may use another job site."),
        "parameters": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "Company name"},
                "website": {"type": "string", "description": "Company website, improves the board guess"},
                "query": {"type": "string", "description": "Optional: keep only roles whose title or team contains this"},
            },
            "required": ["company"],
        },
    },
}


def format_for_llm(result: JobsResult) -> str:
    """Turn a JobsResult into the text the LLM sees as the tool result."""
    if result.board is None:
        return f"No Greenhouse or Lever board with open roles found for {result.company}. Treat hiring as unknown."
    header = (f"Board: {result.board} ({result.board_url}), {result.found_via}. "
              f"{result.total_roles} matching roles, showing {len(result.roles)}.")
    if result.found_via and result.found_via.startswith("guessed"):
        header += f" The board was guessed: check it belongs to {result.company}."
    lines = [header]
    for role in result.roles:
        lines.append(f"- {role.title} | {role.location} | {role.team} | {role.url}")
    return "\n".join(lines)
