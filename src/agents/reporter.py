"""Day 2 role 4: code counts verdicts, rejections and usage; the LLM only writes the summary around them."""

import json
import re
from collections import Counter
from pathlib import Path
from typing import get_args

from src import config
from src.llm import Usage, call_llm
from src.logger import RunLog, Timer
from src.schemas import Verdict

ROLE = "reporter"

SYSTEM_PROMPT = """You write the summary of one lead-finder run for a human reviewer.
You get counts that code has already calculated, as JSON. Rules:
- Use only the numbers given. Never calculate new numbers, percentages or totals, and never guess.
- Write exactly two Markdown sections and nothing else:
  "## Summary": 3 to 5 sentences on what the run did and what came out of it.
  "## Open doubts": 2 to 5 bullets on what a reviewer should not trust yet or should check by hand,
  based on the counts (failed companies, unverified or not_enough_evidence leads, rejections,
  validation retries, errors). Do not invent a doubt about a count that is zero.
- No tables and no other headings: code adds the tables."""


def _log_paths(run_dir: Path, summary: dict) -> list[Path]:
    """The log files behind a run: its own log.jsonl, or each part's log for a merged run."""
    if "merged_from" not in summary:
        return [run_dir / "log.jsonl"]
    # merge copies leads and totals only; the detailed events stay in the part folders.
    # merge wrote Windows paths relative to the project root; "/" works on every OS
    return [config.ROOT_DIR / part.replace("\\", "/") / "log.jsonl" for part in summary["merged_from"]]


def _read_events(paths: list[Path]) -> tuple[list[dict], list[str]]:
    """All log events from the given files. Out: (events, names of missing log files)."""
    events: list[dict] = []
    missing: list[str] = []
    for path in paths:
        if not path.exists():
            missing.append(str(path))  # reported as a doubt, not a crash
            continue
        with path.open(encoding="utf-8") as file:
            events += [json.loads(line) for line in file if line.strip()]
    return events, missing


def count_run(run_dir: Path) -> dict:
    """Count everything about one run in plain Python. In: a run folder. Out: a JSON-ready dict."""
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    leads = json.loads((run_dir / "leads.json").read_text(encoding="utf-8"))
    events, missing_logs = _read_events(_log_paths(run_dir, summary))
    events = [event for event in events if event["role"] != ROLE]  # never count our own earlier reports

    verdicts = Counter(lead["verdict"] for lead in leads)
    tool_calls = Counter(e["role"].removeprefix("tool:") for e in events if e["role"].startswith("tool:"))
    counts = {
        "run": run_dir.name, "mode": summary["mode"], "model": summary["model"],
        "companies": len(summary["companies"]), "leads": len(leads),
        # every verdict listed, even at 0, so "no unverified leads" is stated rather than missing
        "verdicts": {verdict: verdicts.get(verdict, 0) for verdict in get_args(Verdict)},
        "failed_companies": summary["failed"],
        "errors": [e["company"] for e in events if e.get("step") == "error"],
        "validation_retries": sum(len(e["output"]) for e in events if e.get("step") == "validation_retries"),
        "llm_requests": summary["usage"]["requests"],
        "input_tokens": summary["usage"]["input_tokens"],
        "output_tokens": summary["usage"]["output_tokens"],
        "search_calls": summary["search"],
        "tool_calls": dict(tool_calls),
        "seconds": summary["seconds"],
        "missing_logs": missing_logs,
        "per_company": [{"company": lead["company"], "verdict": lead["verdict"], "score": lead["score"]}
                        for lead in leads],
    }
    if summary["mode"] != "pipeline":
        counts["rejections"] = "none possible: single mode has no checker"
        return counts

    rejected_drafts = 0
    reasons: Counter = Counter()
    for event in events:
        if event["role"] == "checker" and not event["output"]["passed"]:
            rejected_drafts += 1
            reasons.update(issue["kind"] for issue in event["output"]["issues"])
    counts["rejected_drafts"] = rejected_drafts
    counts["rejections_by_reason"] = dict(reasons)
    return counts


def numbers_not_in_counts(text: str, counts: dict) -> list[str]:
    """Numbers the LLM wrote that appear nowhere in the counts: a cheap check for made-up figures."""
    # The run name is left out: its date and time digits (2026, 13, 10...) would let made-up numbers pass
    counted = {key: value for key, value in counts.items() if key != "run"}
    allowed = set(re.findall(r"\d+", json.dumps(counted)))
    unknown: list[str] = []
    for number in re.findall(r"\d[\d,.]*\d|\d", text):
        # "117,527" and "788.3" must match the counts' 117527 and 788.3, so check each digit run
        if any(part not in allowed for part in re.findall(r"\d+", number.replace(",", ""))):
            unknown.append(number)
    return unknown


def _tables(counts: dict) -> str:
    """The numbers, written by code so the LLM can never change them."""
    rows = [("Companies", counts["companies"]), ("Leads", counts["leads"]),
            *[(f"Verdict: {verdict}", n) for verdict, n in counts["verdicts"].items()],
            ("Failed companies", ", ".join(counts["failed_companies"]) or "none"),
            ("Rejected drafts", counts.get("rejected_drafts", "n/a (single mode)")),
            ("Rejections by reason", json.dumps(counts["rejections_by_reason"])
             if "rejections_by_reason" in counts else "n/a (single mode)"),
            ("Validation retries", counts["validation_retries"]),
            ("LLM requests", counts["llm_requests"]),
            ("Input / output tokens", f"{counts['input_tokens']} / {counts['output_tokens']}"),
            ("Search calls", json.dumps(counts["search_calls"])),
            ("Tool calls", json.dumps(counts["tool_calls"])),
            ("Cost", "$0 (free tier)")]
    lines = ["| Count | Value |", "| --- | --- |"] + [f"| {name} | {value} |" for name, value in rows]
    lines += ["", "| Company | Verdict | Score |", "| --- | --- | --- |"]
    lines += [f"| {c['company']} | {c['verdict']} | {c['score']} |" for c in counts["per_company"]]
    return "\n".join(lines)


def write_report(run_dir: Path) -> Path:
    """Count a run, ask the LLM for the summary, write runs/<ts>/report.md. Out: the report's path.

    If the LLM call fails, the report is still written with the code's numbers: never crash.
    """
    counts = count_run(run_dir)
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "Counts for this run:\n" + json.dumps(counts, indent=2)}]
    usage = Usage()
    with Timer() as timer:
        try:
            response = call_llm(messages)
            usage = response.usage
            text = (response.message.content or "").strip() or "(The LLM returned no summary.)"
            if response.finish_reason == "length":
                text += "\n\n(Cut off at the output limit.)"
        except Exception as error:  # e.g. the daily limit is used up: the numbers still matter
            text = f"(No LLM summary: {type(error).__name__}: {error})"
    RunLog(ROLE, run_dir=run_dir).event(ROLE, input=messages, output=text, usage=usage, duration=timer.seconds)

    unknown = numbers_not_in_counts(text, counts)
    check_line = ("Every number in the summary appears in the counts." if not unknown else
                  f"**Numbers in the summary that are NOT in the counts (check them):** {', '.join(unknown)}")
    report = "\n\n".join([
        f"# Run report: {counts['run']}",
        f"Mode `{counts['mode']}`, model `{counts['model']}`. Tables are counted by code; "
        "the summary and open doubts are written by the LLM from those counts only.",
        _tables(counts), text, "---", check_line,
        f"Report cost (not in the run totals): {usage.requests} request, "
        f"{usage.input_tokens} input / {usage.output_tokens} output tokens, model `{config.LLM_MODEL}`.",
    ])
    path = run_dir / "report.md"
    path.write_text(report + "\n", encoding="utf-8")
    return path
