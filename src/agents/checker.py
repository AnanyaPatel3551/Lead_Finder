"""Day 2 role 3: code checks (URL, quote, ids, score) then an LLM judgement on verified evidence only."""

import re
from collections.abc import Callable
from urllib.parse import urlparse

from rapidfuzz import fuzz

from src import config
from src.llm import Usage, call_llm_validated
from src.schemas import CheckIssue, CheckResult, Contract, CriteriaFile, Evidence, LeadCard
from src.tools import jobs
from src.tools.fetch import PageResult, fetch_page, read_cached
from src.verdict import compute_verdict

QUOTE_MATCH_THRESHOLD = 90  # partial_ratio: ~1 character in 10 may differ (a typo), not a rewritten clause
JOB_BOARD_HOSTS = ("greenhouse.io", "lever.co")

Fetch = Callable[[str], PageResult]
ReadCache = Callable[[str], PageResult | None]

# Characters gpt-oss and web pages write differently for the same thing
_FOLD = {"‐": "-", "‑": "-", "­": "-", "‒": "-", "–": "-", "—": "-",
         "―": "-", "−": "-", " ": " ", " ": " ", "‘": "'", "’": "'",
         "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"'}


def live_fetch(url: str) -> PageResult:
    """The checker's real fetch: always the live page, never the cache, and the full text."""
    return fetch_page(url, use_cache=False, max_chars=None)


def normalise(text: str) -> str:
    """Fold look-alike characters, collapse whitespace and lowercase, so only real differences remain."""
    for odd, plain in _FOLD.items():
        text = text.replace(odd, plain)
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_on_page(quote: str, page_text: str) -> bool:
    """True if the normalised quote matches some stretch of the normalised page closely enough."""
    return fuzz.partial_ratio(normalise(quote), normalise(page_text)) >= QUOTE_MATCH_THRESHOLD


def fetch_pages(card: LeadCard, fetch: Fetch) -> dict[str, PageResult]:
    """Fetch every cited URL once, even when several evidence records share a page."""
    return {url: fetch(url) for url in sorted({item.source_url for item in card.evidence})}


def check_urls(card: LeadCard, pages: dict[str, PageResult]) -> list[CheckIssue]:
    """Every cited URL must load with readable text: an error or an empty/JS page can't be verified."""
    issues = []
    for item in card.evidence:
        page = pages[item.source_url]
        if page.error or page.empty_or_js:
            reason = page.error or "the page has no readable text (JavaScript or a block page)"
            issues.append(CheckIssue(kind="url_unreachable", target_id=item.id,
                                     message=f"{item.id}: {item.source_url} could not be verified: {reason}"))
    return issues


def _is_job_board(url: str) -> bool:
    """True for find_jobs evidence: a Greenhouse or Lever role URL."""
    return (urlparse(url).hostname or "").endswith(JOB_BOARD_HOSTS)


def _title_on_board(item: Evidence) -> bool:
    """For a role URL whose page is empty: is the quoted title among the board's current roles?"""
    parts = [part for part in urlparse(item.source_url).path.split("/") if part]
    if not parts:
        return False
    lookup = jobs._greenhouse if "greenhouse.io" in item.source_url else jobs._lever
    roles = lookup(parts[0]) or []  # the board name is the first path part on both job sites
    return any(normalise(item.quote) == normalise(role.title) for role in roles)


def _not_found_message(item: Evidence, read_cache: ReadCache) -> str:
    """Why a quote is missing: drift (it was in the copy the agent read), likely made up, or unknown."""
    cached = read_cache(item.source_url)
    if cached is None:
        cause = "cause unknown: no cached copy on this machine"
    elif quote_on_page(item.quote, cached.text):
        cause = "page drift: the quote WAS in the cached copy the agent read, but the live page changed"
    else:
        cause = "likely made up: the quote is in neither the live page nor the cached copy"
    return f"{item.id}: quote not found on {item.source_url} ({cause})"


def check_quotes(card: LeadCard, pages: dict[str, PageResult], read_cache: ReadCache) -> list[CheckIssue]:
    """Every quote must be on its live page; for job-board evidence, the role title on its page or board."""
    issues = []
    for item in card.evidence:
        page = pages[item.source_url]
        if page.error or page.empty_or_js:
            if not _is_job_board(item.source_url):
                continue  # already reported by check_urls; one problem, one issue
            found = _title_on_board(item)
        else:
            found = quote_on_page(item.quote, page.text)
        if not found:
            issues.append(CheckIssue(kind="quote_not_found", target_id=item.id,
                                     message=_not_found_message(item, read_cache)))
    return issues


def check_evidence_ids(card: LeadCard) -> list[CheckIssue]:
    """Every yes/no must cite evidence that exists AND is evidence for that same criterion."""
    by_id = {item.id: item for item in card.evidence}
    issues = []
    for result in card.criteria:
        if result.met == "unknown":
            continue
        item = by_id.get(result.evidence_id)
        if item is None:
            message = (f"criterion '{result.criterion_id}' is '{result.met}' but cites {result.evidence_id}, "
                       "which is not in the evidence list")
        elif item.criterion_id != result.criterion_id:
            message = (f"criterion '{result.criterion_id}' cites {item.id}, which is evidence for "
                       f"'{item.criterion_id}', not '{result.criterion_id}'")
        else:
            continue
        issues.append(CheckIssue(kind="unknown_evidence_id", target_id=result.criterion_id, message=message))
    return issues


def check_score(card: LeadCard, criteria_file: CriteriaFile) -> list[CheckIssue]:
    """The card's score and verdict must be what the shared rule gives for its criteria results."""
    try:
        score, verdict = compute_verdict(card.criteria, criteria_file)
    except ValueError as error:  # e.g. a result for a criterion id that isn't in criteria.json
        return [CheckIssue(kind="score_mismatch", target_id=None, message=f"criteria can't be scored: {error}")]
    if (score, verdict) != (card.score, card.verdict):
        return [CheckIssue(kind="score_mismatch", target_id=None,
                           message=f"card says score {card.score} and {card.verdict}, but its criteria "
                                   f"give score {score} and {verdict}")]
    return []


# Ground rule: quotes state company facts, never people. A heuristic: it can flag a two-word
# slogan as a name; that costs one retry, while missing a real name breaks the rule.
_ROLE = r"(?i:ceo|cto|cfo|coo|cpo|co-?founder|founder|chairman|chairperson|president|vice president|vp|managing director|director|head of|chief \w+ officer)"
_NAME = r"[A-Z][a-z]+(?: [A-Z]\.)?(?: [A-Z][a-z'-]+){1,2}"


def _names_a_person(quote: str) -> bool:
    """True if a quote is only a name, only a title, or a role right next to a name."""
    text = quote.strip(" .,:;\"'")
    if re.fullmatch(_NAME, text):
        return True  # "Jane Doe"
    if re.search(rf"\b{_ROLE}\b", text) and len(text.split()) <= 6:
        return True  # "Co-founder & CEO"
    return bool(re.search(rf"\b{_ROLE}\b[ ,:]+{_NAME}", text) or re.search(rf"{_NAME},? {_ROLE}\b", text))


def check_personal_data(card: LeadCard) -> list[CheckIssue]:
    """No quote may be a person's name or title. Job-board quotes are open roles, not people."""
    issues = []
    for item in card.evidence:
        if not _is_job_board(item.source_url) and _names_a_person(item.quote):
            issues.append(CheckIssue(kind="personal_data", target_id=item.id,
                                     message=f"{item.id}: the quote names a person or is only a title; "
                                             "quote a fact about the company instead"))
    return issues


def check_lead(card: LeadCard, criteria_file: CriteriaFile | None = None,
               fetch: Fetch = live_fetch, read_cache: ReadCache = read_cached) -> list[CheckIssue]:
    """Run every code check on one card. It needs only the card (cards carry their evidence),
    so Day 1's leads can be checked the same way. Out: all issues; empty means it passed.
    """
    if criteria_file is None:
        criteria_file = CriteriaFile.model_validate_json(config.CRITERIA_PATH.read_text(encoding="utf-8"))
    pages = fetch_pages(card, fetch)
    return (check_urls(card, pages) + check_quotes(card, pages, read_cache) + check_evidence_ids(card)
            + check_score(card, criteria_file) + check_personal_data(card))


JUDGE_PROMPT = """You are a strict fact-checker. For each claim below you get a criterion, the claimed
answer ("yes" or "no") and one quote from a web page. Judge only whether the quote, read literally,
proves the claimed answer for that criterion.
- Use only the quotes. Do not use anything you know about the company from elsewhere.
- A quote that is merely related to the topic, or about something else, does not prove the claim.
- A "no" is proven only if the quote shows the criterion is NOT met.
- For an exclusion criterion, "yes" means the exclusion applies to the company.
- For an exclusion claimed "no", a quote showing the company's main product is something else is enough.
Also list any exclusion that the quotes show DOES apply although it was not claimed "yes"."""


class JudgedClaim(Contract):
    """The judge's finding on one yes/no claim."""

    criterion_id: str
    proven: bool
    reason: str


class MissedExclusion(Contract):
    """An exclusion the quotes show applies, although the card didn't claim it."""

    criterion_id: str
    reason: str


class Judgement(Contract):
    """What the judge returns: one finding per yes/no claim, plus any missed exclusion."""

    claims: list[JudgedClaim]
    missed_exclusions: list[MissedExclusion]


def _judge_messages(card: LeadCard, criteria_file: CriteriaFile) -> list[dict]:
    """The judge's view: criteria, claimed answers and verified quotes. No claim text, no reasoning."""
    evidence = {item.id: item for item in card.evidence}
    results = {result.criterion_id: result for result in card.criteria}
    lines = []
    for criterion in criteria_file.criteria:
        result = results.get(criterion.id)
        met = result.met if result else "unknown"
        lines.append(f"- {criterion.id} ({criterion.kind}): {criterion.description}\n  claimed: {met}")
        if result and result.evidence_id in evidence:
            item = evidence[result.evidence_id]
            lines.append(f'  quote: "{item.quote}"\n  source: {item.source_url}')
    user_text = (f"Company: {card.company}\n\nClaims:\n" + "\n".join(lines)
                 + "\n\nJudge every claim that is 'yes' or 'no'. Ignore 'unknown' ones.")
    return [{"role": "system", "content": JUDGE_PROMPT}, {"role": "user", "content": user_text}]


def judge_lead(card: LeadCard, criteria_file: CriteriaFile) -> tuple[list[CheckIssue], Usage]:
    """The LLM judgement, for a card that passed the code checks. Out: (issues, usage)."""
    response = call_llm_validated(_judge_messages(card, criteria_file), Judgement,
                                  temperature=config.JUDGE_TEMPERATURE)
    if response.parsed is None:
        # Fail closed: a lead nobody could check is not accepted
        return [CheckIssue(kind="judgement", target_id=None,
                           message=f"the judge could not give a valid answer: {response.validation_error[:200]}")], response.usage
    issues = []
    for claim in response.parsed.claims:
        if not claim.proven:
            issues.append(CheckIssue(kind="judgement", target_id=claim.criterion_id,
                                     message=f"{claim.criterion_id}: the quote does not prove it: {claim.reason}"))
    for missed in response.parsed.missed_exclusions:
        issues.append(CheckIssue(kind="judgement", target_id=missed.criterion_id,
                                 message=f"{missed.criterion_id}: the quotes show this exclusion applies: {missed.reason}"))
    return issues, response.usage


def check(card: LeadCard, criteria_file: CriteriaFile | None = None, fetch: Fetch = live_fetch,
          read_cache: ReadCache = read_cached) -> tuple[CheckResult, Usage]:
    """The checker role: code checks first; the LLM judge only if they all pass (saves tokens).

    Out: (CheckResult, usage). usage is zero when the code checks already rejected the card.
    """
    if criteria_file is None:
        criteria_file = CriteriaFile.model_validate_json(config.CRITERIA_PATH.read_text(encoding="utf-8"))
    issues = check_lead(card, criteria_file, fetch, read_cache)
    usage = Usage()
    has_claims = any(result.met != "unknown" for result in card.criteria)
    # With no yes/no claims there is nothing to judge; a missed exclusion needs a quote, and an
    # all-unknown card cites none. So skipping changes the cost, never the result.
    if not issues and has_claims:
        issues, usage = judge_lead(card, criteria_file)
    return CheckResult(passed=not issues, issues=issues), usage
