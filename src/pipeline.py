"""Day 2 orchestration for one company: researcher -> drafter -> checker, redrafting on rejection."""

from src import config
from src.agents import checker
from src.agents.drafter import Rejected, drop_failed_evidence, rejected_pairs, run_drafter
from src.agents.researcher import run_researcher
from src.llm import Usage
from src.logger import RunLog, Timer
from src.schemas import CheckIssue, CriteriaFile, LeadCard
from src.tools.fetch import PageResult

ROLE = "pipeline"


def memo_fetch() -> checker.Fetch:
    """A live fetch that downloads each URL once: one per company, shared by all its check attempts."""
    pages: dict[str, PageResult] = {}

    def fetch(url: str) -> PageResult:
        """The checker's live fetch, remembered for the rest of this company's attempts."""
        if url not in pages:
            pages[url] = checker.live_fetch(url)
        return pages[url]

    return fetch


def run_company(company: str, website: str, criteria_file: CriteriaFile,
                log: RunLog) -> tuple[LeadCard | None, Usage]:
    """Research, draft and check one company.

    In: company and website exactly as in test_set.csv, the loaded criteria, the run log.
    Out: (LeadCard, usage). The card is "unverified" if the checker never accepted a draft;
    None only if no draft could be made at all (logged). Never raises.
    """
    usage = Usage()
    try:
        return _run(company, website, criteria_file, log, usage), usage
    except Exception as error:  # e.g. a network error inside the checker
        log.event(ROLE, company, output=f"error: {type(error).__name__}: {error}", usage=usage, step="error")
        return None, usage


def _run(company: str, website: str, criteria_file: CriteriaFile, log: RunLog, usage: Usage) -> LeadCard | None:
    """The steps of run_company. Adds every role's cost to usage."""
    evidence, research_usage = run_researcher(company, website, criteria_file, log)
    usage.add(research_usage)
    if evidence is None:
        return None  # the researcher logged why

    fetch = memo_fetch()
    issues: list[CheckIssue] = []
    rejected: Rejected = set()
    last_card: LeadCard | None = None

    for attempt in range(1 + config.MAX_RETRIES):  # the first draft, then up to MAX_RETRIES redrafts
        card, draft_usage = run_drafter(company, evidence, criteria_file, log, issues or None, rejected)
        usage.add(draft_usage)
        if card is None:
            break  # an invalid redraft: fall back to the last checked draft, if there is one

        with Timer() as timer:
            result, check_usage = checker.check(card, criteria_file, fetch=fetch)
        usage.add(check_usage)
        log.event("checker", company, input=card, output=result, usage=check_usage,
                  duration=timer.seconds, attempt=attempt)
        if result.passed:
            log.event(ROLE, company, output=card, usage=usage, step="lead_card", attempts=attempt + 1)
            return card

        last_card = card
        issues = result.issues
        evidence = drop_failed_evidence(evidence, issues)  # bad records are gone for every later draft
        rejected |= rejected_pairs(card, issues)           # judge rejections pile up across attempts

    if last_card is None:
        return None  # not even the first draft was valid
    # Reported, never dropped: the reviewer sees the last draft and, in the log, why it failed
    card = last_card.model_copy(update={"verdict": "unverified"})
    log.event(ROLE, company, output=card, usage=usage, step="unverified", last_issues=issues)
    return card
