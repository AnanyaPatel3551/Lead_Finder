"""Day 2 role 2: one LLM call, no tools. Evidence in, criterion answers out; Python builds the lead card."""

from src.llm import Usage, call_llm_validated
from src.logger import RunLog, Timer
from src.schemas import CheckIssue, Contract, CriteriaFile, CriterionResult, Evidence, LeadCard
from src.verdict import compute_verdict

ROLE = "drafter"

# No ICP content here: the criteria come from criteria.json at run time
DRAFTER_PROMPT = """You turn evidence records about one company into an answer for each criterion.
You have no tools and no other information: use only the evidence records you are given.
- Answer EVERY criterion with met = "yes", "no" or "unknown".
- "yes" or "no" must give the evidence_id of a record for THAT criterion whose quote shows it.
  If no record shows it, answer "unknown" with evidence_id null.
- "no" needs a quote showing the criterion is NOT met. Missing evidence is "unknown", never "no".
- For an exclusion criterion, "yes" means the exclusion APPLIES to the company.
- Prefer concrete evidence (job roles, product features, stated numbers) over slogans and taglines.
- why_it_fits: two or three sentences on why the company does or does not fit, using only the
  evidence you cite. You do not decide the overall verdict; code does that from your answers."""

# (criterion_id, evidence_id) pairs the checker's judge said do not prove that criterion
Rejected = set[tuple[str, str]]


class DraftOutput(Contract):
    """The drafter's answer. Smaller than LeadCard on purpose: no evidence, score or verdict to get wrong."""

    criteria: list[CriterionResult]
    why_it_fits: str


def draft_messages(company: str, evidence: list[Evidence], criteria_file: CriteriaFile,
                   feedback: str | None = None) -> list[dict]:
    """The drafter's whole input: criteria, evidence records and, on a retry, the checker's feedback."""
    criteria_lines = [f"- {c.id} ({c.kind}): {c.description}" for c in criteria_file.criteria]
    evidence_lines = [f'- {item.id} (for {item.criterion_id}): {item.claim}\n  quote: "{item.quote}"\n'
                      f"  source: {item.source_url}" for item in evidence]
    user_text = (f"Company: {company}\n\nCriteria:\n" + "\n".join(criteria_lines)
                 + "\n\nEvidence records:\n" + ("\n".join(evidence_lines) or "(none: nothing was found)"))
    if feedback:
        user_text += f"\n\n{feedback}"
    return [{"role": "system", "content": DRAFTER_PROMPT}, {"role": "user", "content": user_text}]


def _unknown(criterion_id: str) -> CriterionResult:
    """The safe answer when a claim can't be backed by usable evidence."""
    return CriterionResult(criterion_id=criterion_id, met="unknown", evidence_id=None)


def enforce_citations(answers: list[CriterionResult], evidence: list[Evidence], criteria_file: CriteriaFile,
                      rejected: Rejected) -> tuple[list[CriterionResult], list[str]]:
    """Python's rules on the drafter's answers, whatever the LLM wrote.

    Out: exactly one result per criterion in criteria.json (in file order), and a note for each
    answer that was changed, for the log. A yes/no citing missing, wrong or rejected evidence
    becomes "unknown"; answers for criterion ids not in the file are dropped.
    """
    by_id = {item.id: item for item in evidence}
    given: dict[str, CriterionResult] = {}
    for answer in answers:
        given.setdefault(answer.criterion_id, answer)  # a duplicate answer: the first one counts
    changes = [f"{cid}: not a criterion in criteria.json, dropped" for cid in given
               if cid not in {c.id for c in criteria_file.criteria}]

    results = []
    for criterion in criteria_file.criteria:
        answer = given.get(criterion.id)
        problem = None
        if answer is None:
            problem = "no answer given"
        elif answer.met != "unknown":
            item = by_id.get(answer.evidence_id)
            if item is None or item.criterion_id != criterion.id:
                problem = f"'{answer.met}' cites {answer.evidence_id}, which is missing or for another criterion"
            elif (criterion.id, item.id) in rejected:
                problem = f"'{answer.met}' cites {item.id}, which the checker already rejected for it"
        if problem:
            changes.append(f"{criterion.id}: {problem}; set to unknown")
            answer = _unknown(criterion.id)
        elif answer.met == "unknown":
            answer = _unknown(criterion.id)  # an "unknown" citing evidence would put unchecked quotes on the card
        results.append(answer)
    return results, changes


def build_card(company: str, results: list[CriterionResult], why_it_fits: str, evidence: list[Evidence],
               criteria_file: CriteriaFile) -> LeadCard:
    """The full LeadCard: score and verdict from the shared rule, evidence = only the records cited."""
    score, verdict = compute_verdict(results, criteria_file)  # can't raise: one result per known criterion
    cited = {result.evidence_id for result in results if result.evidence_id}
    return LeadCard(company=company, verdict=verdict, score=score, criteria=results,
                    evidence=[item for item in evidence if item.id in cited], why_it_fits=why_it_fits)


# Code-check failures mean the record itself is bad (wrong URL, quote not on the page, a person):
# it is removed for good. A judgement rejects a record only for one criterion: see rejected_pairs.
FAILED_EVIDENCE_KINDS = ("url_unreachable", "quote_not_found", "personal_data")


def drop_failed_evidence(evidence: list[Evidence], issues: list[CheckIssue]) -> list[Evidence]:
    """The evidence list without the records that failed a code check."""
    failed = {issue.target_id for issue in issues if issue.kind in FAILED_EVIDENCE_KINDS}
    return [item for item in evidence if item.id not in failed]


def rejected_pairs(card: LeadCard, issues: list[CheckIssue]) -> Rejected:
    """The (criterion, evidence) pairs the judge said don't prove the claim, from the card it judged."""
    cited = {result.criterion_id: result.evidence_id for result in card.criteria}
    pairs: Rejected = set()
    for issue in issues:
        if issue.kind == "judgement" and cited.get(issue.target_id):
            pairs.add((issue.target_id, cited[issue.target_id]))
    return pairs


def feedback_text(issues: list[CheckIssue], rejected: Rejected) -> str:
    """The retry message: what the checker found, and what may not be cited again."""
    lines = ["The checker rejected your previous answer:"] + [f"- {issue.message}" for issue in issues]
    lines.append("Records that failed the checks have been removed from the evidence list above.")
    if rejected:
        pairs = ", ".join(f"{evidence_id} for {criterion_id}" for criterion_id, evidence_id in sorted(rejected))
        lines.append(f"Do not cite these again: {pairs}.")
    lines.append('For each rejected criterion, cite another record for it whose quote proves it, or answer "unknown".')
    return "\n".join(lines)


def run_drafter(company: str, evidence: list[Evidence], criteria_file: CriteriaFile, log: RunLog,
                issues: list[CheckIssue] | None = None,
                rejected: Rejected | None = None) -> tuple[LeadCard | None, Usage]:
    """Draft one lead card from evidence records.

    In: the usable evidence (failed records already dropped), the criteria, the run log and, on a
    retry, the checker's issues and every rejected pair so far. Out: (LeadCard, usage), or
    (None, usage) if the answer was invalid after one retry. Never raises.
    """
    usage = Usage()
    rejected = rejected or set()
    try:
        feedback = feedback_text(issues, rejected) if issues else None
        messages = draft_messages(company, evidence, criteria_file, feedback)
        with Timer() as timer:
            response = call_llm_validated(messages, DraftOutput)
        usage.add(response.usage)
        # The input holds only evidence records and checker messages, never page text
        log.event(ROLE, company, input=messages, output=response.parsed or response.validation_error,
                  usage=response.usage, duration=timer.seconds, step="draft",
                  retry=bool(issues), finish_reason=response.finish_reason)
        if response.parsed is None:
            log.event(ROLE, company, output="skipped: draft invalid after one retry", step="skipped")
            return None, usage

        results, changes = enforce_citations(response.parsed.criteria, evidence, criteria_file, rejected)
        if changes:
            log.event(ROLE, company, output=changes, step="enforced")
        return build_card(company, results, response.parsed.why_it_fits, evidence, criteria_file), usage
    except Exception as error:  # e.g. 429 retries used up
        log.event(ROLE, company, output=f"error: {type(error).__name__}: {error}", usage=usage, step="error")
        return None, usage
