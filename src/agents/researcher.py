"""Day 2 role 1: an LLM tool loop that collects evidence records. It never gives a verdict."""

from pydantic import model_validator

from src import config
from src.llm import Usage, call_llm, call_llm_validated
from src.logger import RunLog, Timer, fingerprint, redact_tool_messages
from src.schemas import Contract, CriteriaFile, Evidence
from src.tools.registry import TOOL_DEFINITIONS, run_tool

ROLE = "researcher"

# The quote rules and the "Prefer concrete evidence" line are copied word for word from
# single_agent.py, not imported: Day 1 must not depend on Day 2 code. evals/test_prompts.py
# fails if the two copies ever differ. No ICP content here: criteria come from criteria.json.
SYSTEM_PROMPT = """You research one company and collect evidence for each criterion you are given.
You do not decide whether the company meets a criterion or fits; another step does that from your
evidence.

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
- An empty, blocked or JavaScript-only page tells you nothing: record no evidence from it.
  Not finding something is not evidence. Record evidence that a criterion is NOT met only
  with a quote that shows it is not met.
- For an exclusion criterion, look for evidence of whether the exclusion APPLIES to the company.

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

FINAL_INSTRUCTION = """Research is over. You can no longer call tools. Now list your evidence as JSON:
- evidence: one record per fact you found: id (E1, E2, ...), criterion_id (one of the criterion
  ids listed above), a short claim, the source_url, and the quote copied exactly from a tool result.
- Include facts that show a criterion is met AND facts that show it is not met. A criterion may
  have more than one record.
- Do not decide whether the company meets a criterion or fits. If you found nothing for a
  criterion, give no record for it."""


class ResearchOutput(Contract):
    """What the researcher hands to the drafter: evidence records only, no decisions."""

    evidence: list[Evidence]

    @model_validator(mode="after")
    def ids_are_unique(self) -> "ResearchOutput":
        """The drafter cites evidence by id, so two records with one id would make a citation ambiguous."""
        seen = set()
        for item in self.evidence:
            if item.id in seen:
                raise ValueError(f"evidence id '{item.id}' is used twice: give every record its own id")
            seen.add(item.id)
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
    rejects the request. Results are given in full here: the evidence must quote them exactly.
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


def run_researcher(company: str, website: str, criteria_file: CriteriaFile,
                   log: RunLog) -> tuple[list[Evidence] | None, Usage]:
    """Research one company and return its evidence records.

    In: company and website exactly as in test_set.csv, the loaded criteria, the run log.
    Out: (evidence, usage). An empty list means nothing was found; None means the research
    failed (logged). Never raises, so a failed company still reports what it cost.
    """
    usage = Usage()
    try:
        return _run(company, website, criteria_file, log, usage), usage
    except Exception as error:  # e.g. RequestTooLargeError, or 429 retries used up
        log.event(ROLE, company, output=f"error: {type(error).__name__}: {error}", usage=usage, step="error")
        return None, usage


def _run(company: str, website: str, criteria_file: CriteriaFile, log: RunLog,
         usage: Usage) -> list[Evidence] | None:
    """The steps of run_researcher: tool loop, then the evidence list. Adds every cost to usage."""
    messages = build_messages(company, website, criteria_file)
    _research(messages, company, log, usage)

    # Groq can't combine tools and a JSON schema, so the evidence is a separate call with no tools
    final_messages, transcript = _final_messages(messages)
    with Timer() as timer:
        final = call_llm_validated(final_messages, ResearchOutput)
    usage.add(final.usage)
    log.event(ROLE, company, input={"transcript": fingerprint(transcript)},
              output=final.parsed if final.parsed else final.validation_error,
              usage=final.usage, duration=timer.seconds, step="evidence", finish_reason=final.finish_reason)
    if final.parsed is None:
        log.event(ROLE, company, output="skipped: evidence list invalid after one retry", step="skipped")
        return None
    return final.parsed.evidence
