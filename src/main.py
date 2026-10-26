"""Command-line entry point: python -m src.main evaluate --mode single|pipeline.

  check "<company>" --website <url> --mode single     one company, printed and saved
  evaluate --mode single [--part 1/3]                 the test set, or one third of it
  merge <run_dir> <run_dir> ...                       combine the parts into one run
"""

import argparse
import hashlib
import json
from pathlib import Path

from src import config
from src.agents.single_agent import run_single_agent
from src.llm import Usage
from src.logger import RunLog, Timer
from src.schemas import CriteriaFile, LeadCard
from src.tools.search import search_usage
from evals.scoring import load_test_set

# Must match across parts and across Day 1 / Day 2, or the comparison is not fair
MUST_MATCH = ("mode", "model", "criteria_version", "criteria_sha256", "test_set_sha256")


def _file_sha256(path: Path) -> str:
    """Fingerprint of a file, so a run records exactly which criteria and test set it used."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _save(log: RunLog, cards: list[LeadCard], summary: dict) -> None:
    """Write leads.json and summary.json into the run folder (called after every company)."""
    leads = [card.model_dump() for card in cards]
    (log.run_dir / "leads.json").write_text(json.dumps(leads, indent=2, ensure_ascii=False), encoding="utf-8")
    (log.run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def run_companies(companies: list[tuple[str, str]], mode: str, label: str, part: str | None) -> Path:
    """Run the chosen architecture on (company, website) pairs. Out: the run folder."""
    if mode != "single":
        raise SystemExit("--mode pipeline is not built yet (Day 2).")
    criteria_file = CriteriaFile.model_validate_json(config.CRITERIA_PATH.read_text(encoding="utf-8"))
    log = RunLog(label)
    usage = Usage()
    cards: list[LeadCard] = []
    summary = {"mode": mode, "model": config.LLM_MODEL, "part": part,
               "criteria_version": criteria_file.version,
               "criteria_sha256": _file_sha256(config.CRITERIA_PATH),
               "test_set_sha256": _file_sha256(config.TEST_SET_PATH),
               "companies": [name for name, _ in companies], "failed": []}

    with Timer() as run_timer:
        for company, website in companies:
            print(f"- {company} ...", flush=True)
            try:
                card, company_usage = run_single_agent(company, website, criteria_file, log)
            except Exception as error:  # a too-large request or exhausted 429 retries: log it, keep going
                log.event("run", company, output=f"error: {type(error).__name__}: {error}", step="error")
                card, company_usage = None, Usage()
            usage.add(company_usage)
            if card is None:
                summary["failed"].append(company)
            else:
                cards.append(card)
                print(f"  {card.verdict} (score {card.score})")
            summary.update(usage=vars(usage), search=search_usage(), seconds=round(run_timer.elapsed(), 1))
            _save(log, cards, summary)

    log.event("run", output=summary, step="run_summary")
    print(f"\nSaved to {log.run_dir}  |  {len(cards)} leads, failed: {summary['failed'] or 'none'}")
    print(f"Usage: {usage}  |  search: {search_usage()}  |  {summary['seconds']} s")
    return log.run_dir


def _slice(rows: list[dict], part: str) -> list[dict]:
    """Rows for part 'k/n': the k-th of n contiguous slices, the same every time for the same file."""
    k, n = (int(x) for x in part.split("/"))
    if not 1 <= k <= n:
        raise SystemExit(f"--part must look like 1/3, got {part}")
    return rows[(k - 1) * len(rows) // n: k * len(rows) // n]


def merge(run_dirs: list[Path]) -> Path:
    """Combine the parts' leads and usage into one new run folder. Refuses mismatched parts."""
    summaries = [json.loads((d / "summary.json").read_text(encoding="utf-8")) for d in run_dirs]
    for key in MUST_MATCH:
        values = {s[key] for s in summaries}
        if len(values) > 1:
            raise SystemExit(f"Cannot merge: the parts differ on {key}: {sorted(values)}")

    cards: list[dict] = []
    total = {"usage": Usage(), "search": {}, "seconds": 0.0, "companies": [], "failed": []}
    for run_dir, summary in zip(run_dirs, summaries):
        cards += json.loads((run_dir / "leads.json").read_text(encoding="utf-8"))
        total["usage"].add(Usage(**summary["usage"]))
        for engine, count in summary["search"].items():
            total["search"][engine] = total["search"].get(engine, 0) + count
        total["seconds"] += summary["seconds"]  # summed machine time; the parts ran in parallel
        total["companies"] += summary["companies"]
        total["failed"] += summary["failed"]

    log = RunLog(f"merged_{summaries[0]['mode']}")
    merged = {key: summaries[0][key] for key in MUST_MATCH}
    merged.update(parts=[s["part"] for s in summaries], merged_from=[str(d) for d in run_dirs],
                  companies=total["companies"], failed=total["failed"], usage=vars(total["usage"]),
                  search=total["search"], seconds=round(total["seconds"], 1))
    _save(log, [LeadCard.model_validate(card) for card in cards], merged)
    print(f"Merged {len(run_dirs)} parts -> {log.run_dir}  |  {len(cards)} leads, failed: {total['failed'] or 'none'}")
    return log.run_dir


def main() -> None:
    """Parse the command line and run check, evaluate or merge."""
    parser = argparse.ArgumentParser(description="Lead Finder")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="run one company")
    check.add_argument("company")
    check.add_argument("--website", required=True)
    check.add_argument("--mode", choices=["single", "pipeline"], default="single")
    evaluate = commands.add_parser("evaluate", help="run the labelled test set")
    evaluate.add_argument("--mode", choices=["single", "pipeline"], required=True)
    evaluate.add_argument("--part", help="e.g. 1/3: run only that third of test_set.csv")
    merge_cmd = commands.add_parser("merge", help="combine part runs into one")
    merge_cmd.add_argument("run_dirs", nargs="+", type=Path)
    args = parser.parse_args()

    if args.command == "check":
        run_companies([(args.company, args.website)], args.mode, f"check_{args.mode}", None)
    elif args.command == "evaluate":
        rows = load_test_set(config.TEST_SET_PATH)
        if args.part:
            rows = _slice(rows, args.part)
        if not rows:
            raise SystemExit("No companies to run: data/test_set.csv has no labelled rows yet.")
        label = f"evaluate_{args.mode}" + (f"_part{args.part.replace('/', 'of')}" if args.part else "")
        # Company and website go through exactly as written in test_set.csv, so scoring can match them
        run_companies([(row["company"], row["website"]) for row in rows], args.mode, label, args.part)
    else:
        merge(args.run_dirs)


if __name__ == "__main__":
    main()
