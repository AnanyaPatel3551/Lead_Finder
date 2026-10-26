"""Day 1 baseline: one LLM tool loop that researches, judges and writes the lead card. Frozen after day1-baseline."""

from pydantic import model_validator

from src import config
from src.llm import Usage, call_llm, call_llm_validated
from src.logger import RunLog, Timer, fingerprint, redact_tool_messages
from src.schemas import Contract, CriteriaFile, CriterionResult, Evidence, LeadCard
from src.tools.registry import TOOL_DEFINITIONS, run_tool
from src.verdict import compute_verdict

ROLE = "single_agent"

# How to research and judge. No ICP content here: the criteria come from criteria.json at run time.
SYSTEM_PROMPT = """You research one company and decide, for each criterion you are given, whether the
company meets it. You do not decide the overall verdict; code does that from your answers.

Tools: web_search, fetch_page, find_jobs. Call one tool at a time. You have at most {max_tool_calls}
tool calls, so plan: the company's own pages first (about, careers, product), then find_jobs with the
company's website, then a web search for anything still missing.

Evidence rules:
- The company's own pages are the main evidence. Careers-page text is the main hiring evidence;
  job-board roles from find_jobs are a bonus. Search snippets help you find pages; quote a fetched
  page when you can.
- Prefer concrete evidence (job roles, product features, stated numbers) over slogans and taglines.
- Use the website you are given to make sure every page is about THIS company, not another
  company with a similar name.
- An empty, blocked or JavaScript-only page tells you nothing: the criterion stays "unknown".
  Not finding something is "unknown", never "no". Answer "no" only with a quote that shows the
  criterion is not met.
- For an exclusion criterion, "yes" means the exclusion APPLIES to the company.

Quote rules:
- Copy every quote exactly, character for character, from a tool result you received. Never
  write a quote you did not see in a tool result.
- Keep each quote short: one sentence or phrase, under 200 characters.
- From find_jobs results, quote only the role title (not the location, team or URL); use
  the role's url as the source_url.
- Quotes must state facts about the company and must never name a person. "A team of 400
  engineers" is fine; "CEO Jane Doe says..." is not.
- Older tool results are shortened as the conversation grows. So before each new tool call,
  write in your message the exact quotes (with their URLs) you may need later."""

FINAL_INSTRUCTION = """Research is over. You can no longer call tools; if something is still missing,
answer "unknown". Now give your final answer as JSON:
- criteria: one result for EVERY criterion id listed above, with met = "yes", "no" or "unknown".
- evidence: one record for each "yes" or "no": id (E1, E2, ...), criterion_id, a short claim,
  the source_url, and the quote copied exactly from a tool result.
- Every "yes" and "no" must give the evidence_id of its record; "unknown" has evidence_id null.
- why_it_fits: two or three sentences on why the company does or does not fit, using only your evidence."""


class FinalAnswer(Contract):
    """What the single agent returns at the end. No score or verdict: Python computes those."""

    criteria: list[CriterionResult]
    evidence: list[Evidence]
    why_it_fits: str

    @model_validator(mode="after")
    def cited_evidence_exists(self) -> "FinalAnswer":
        """Internal consistency only: every cited evidence_id must be in the evidence list."""
        evidence_ids = {item.id for item in self.evidence}
        for result in self.criteria:
            if result.evidence_id is not None and result.evidence_id not in evidence_ids:
                raise ValueError(f"criterion '{result.criterion_id}' cites evidence_id '{result.evidence_id}', "
                                 "which is not in evidence: add that record, or set met to 'unknown'")
        return self


def build_messages(company: str, website: str, criteria_file: CriteriaFile) -> list[dict]:
    """The starting conversation: rules, then the company and its criteria (rendered from the file)."""
    lines = []
    for criterion in criteria_file.criteria:
        lines.append(f"- {criterion.id} ({criterion.kind}): {criterion.description}")
    user_text = (f"Company: {company}\nWebsite: {website}\n\nCriteria:\n" + "\n".join(lines)
                 + "\n\nStart your research.")
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(max_tool_calls=config.MAX_TOOL_CALLS)},
        {"role": "user", "content": user_text},
    ]


def _assistant_message(message: object) -> dict:
    """The model's reply as a plain dict for the history. gpt-oss's hidden reasoning is dropped."""
    record: dict = {"role": "assistant", "content": message.content or ""}
    if message.tool_calls:
        record["tool_calls"] = [{"id": call.id, "type": "function",
                                 "function": {"name": call.function.name, "arguments": call.function.arguments}}
                                for call in message.tool_calls]
    return record


def _research(messages: list[dict], company: str, log: RunLog, usage: Usage) -> None:
    """The tool loop: the model calls tools until it stops or MAX_TOOL_CALLS are used.

    Appends every reply and tool result to messages (in place); adds every call's cost to usage.
    """
    tool_calls_used = 0
    while tool_calls_used < config.MAX_TOOL_CALLS:
        with Timer() as timer:
            response = call_llm(messages, tools=TOOL_DEFINITIONS)
        usage.add(response.usage)
        if response.message is None:
            # Groq rejected a malformed tool call: keep what we found and go to the final answer
            log.event(ROLE, company, output=response.validation_error, usage=response.usage, step="research_ended")
            return
        reply = _assistant_message(response.message)
        log.event(ROLE, company, input=redact_tool_messages(messages), output=reply,
                  usage=response.usage, duration=timer.seconds, step="research")
        messages.append(reply)
        if not response.message.tool_calls:
            return  # the model decided its research is done

        for call in response.message.tool_calls:
            if tool_calls_used >= config.MAX_TOOL_CALLS:
                # Every tool request needs an answer in the history, even one we refuse
                result = "Tool limit reached: this call was not run."
            else:
                with Timer() as tool_timer:
                    result = run_tool(call.function.name, call.function.arguments)
                tool_calls_used += 1
                log.event(f"tool:{call.function.name}", company, input=call.function.arguments,
                          output=fingerprint(result), duration=tool_timer.seconds)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})


def _final_messages(messages: list[dict]) -> tuple[list[dict], str]:
    """Rewrite the research as one plain-text transcript for the final, no-tools call.

    With tool calls left in the history, the model tries to call yet another tool and Groq
    rejects the request. Results are given in full here: the answer must quote them exactly.
    Out: (the final messages, the transcript text, which must be fingerprinted, not logged).
    """
    parts = []
    for message in messages[2:]:  # after the system prompt and the company/criteria message
        if message["role"] == "assistant":
            if message.get("content"):
                parts.append(f"[your note] {message['content']}")
            for call in message.get("tool_calls", []):
                parts.append(f"[tool call] {call['function']['name']} {call['function']['arguments']}")
        elif message["role"] == "tool":
            parts.append(f"[tool result]\n{message['content']}")
    transcript = "\n\n".join(parts) or "(no research was done)"
    final_text = f"Your research so far:\n\n{transcript}\n\n{FINAL_INSTRUCTION}"
    return messages[:2] + [{"role": "user", "content": final_text}], transcript


def run_single_agent(company: str, website: str, criteria_file: CriteriaFile,
                     log: RunLog) -> tuple[LeadCard | None, Usage]:
    """Research one company and build its lead card.

    In: company and website exactly as in test_set.csv, the loaded criteria, the run log.
    Out: (LeadCard, usage), or (None, usage) if anything failed (logged). Never raises, so a
    failed company still reports what it cost.
    """
    usage = Usage()
    try:
        return _run(company, website, criteria_file, log, usage), usage
    except Exception as error:  # e.g. RequestTooLargeError, or 429 retries used up
        log.event(ROLE, company, output=f"error: {type(error).__name__}: {error}", usage=usage, step="error")
        return None, usage


def _run(company: str, website: str, criteria_file: CriteriaFile, log: RunLog, usage: Usage) -> LeadCard | None:
    """The steps of run_single_agent: research, final answer, verdict. Adds every cost to usage."""
    messages = build_messages(company, website, criteria_file)
    _research(messages, company, log, usage)

    # Groq can't combine tools and a JSON schema, so the answer is a separate call with no tools
    final_messages, transcript = _final_messages(messages)
    with Timer() as timer:
        final = call_llm_validated(final_messages, FinalAnswer)
    usage.add(final.usage)
    log.event(ROLE, company, input={"transcript": fingerprint(transcript)},
              output=final.parsed if final.parsed else final.validation_error,
              usage=final.usage, duration=timer.seconds, step="final", finish_reason=final.finish_reason)
    if final.parsed is None:
        log.event(ROLE, company, output="skipped: final answer invalid after one retry", step="skipped")
        return None

    answer = final.parsed
    try:
        # The verdict comes from the shared rule, never from the model
        score, verdict = compute_verdict(answer.criteria, criteria_file)
    except ValueError as error:  # e.g. a result for a criterion id that doesn't exist
        log.event(ROLE, company, output=f"skipped: {error}", step="skipped")
        return None

    card = LeadCard(company=company, verdict=verdict, score=score, criteria=answer.criteria,
                    evidence=answer.evidence, why_it_fits=answer.why_it_fits)
    log.event(ROLE, company, output=card, usage=usage, step="lead_card")
    return card
