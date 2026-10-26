"""Scores a run's verdicts against the labelled test set, identically for single and pipeline modes.

Usage: python -m evals.scoring runs/<timestamp>/leads.json [--test-set data/test_set.csv]
"""

import argparse
import csv
from pathlib import Path

from pydantic import TypeAdapter

from src import config
from src.schemas import LeadCard

LABELS = ("fit", "not_fit")  # humans always decide, so ground truth has no "not_enough_evidence"


def _key(company: str) -> str:
    """Normalise a company name for matching, so 'Acme Ltd ' and 'acme ltd' are the same row."""
    return " ".join(company.lower().split())


def load_test_set(path: Path) -> list[dict[str, str]]:
    """Read test_set.csv and enforce the labelling rules. Raises ValueError on a bad row."""
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    for number, row in enumerate(rows, start=2):  # row 1 is the header
        for column in ("company", "website", "label"):
            if not (row.get(column) or "").strip():
                raise ValueError(f"{path} row {number}: '{column}' is empty")
        if row["label"] not in LABELS:
            raise ValueError(f"{path} row {number}: label '{row['label']}' must be one of {LABELS}")
        if row["label_a"] != row["label_b"] and not row["note"].strip():
            raise ValueError(f"{path} row {number}: labellers disagree, so 'note' must say how it was settled")
    return rows


def load_leads(path: Path) -> list[LeadCard]:
    """Read a run's leads.json (a list of LeadCards), validating every card against the schema."""
    return TypeAdapter(list[LeadCard]).validate_json(path.read_text(encoding="utf-8"))


def predicted_label(verdict: str) -> str:
    """Collapse a verdict to fit/not_fit: not_enough_evidence and unverified count as not_fit."""
    return "fit" if verdict == "fit" else "not_fit"


def percent(numerator: int, denominator: int) -> str:
    """Format a ratio with its counts; 'n/a' when nothing was counted, instead of dividing by zero."""
    if denominator == 0:
        return "n/a (0/0)"
    return f"{100 * numerator / denominator:.0f}% ({numerator}/{denominator})"


def score(leads: list[LeadCard], rows: list[dict[str, str]]) -> None:
    """Print verdict counts, precision, recall, the confusion table, disagreements and labeller agreement."""
    leads_by_key = {_key(lead.company): lead for lead in leads}
    counts = {("fit", "fit"): 0, ("fit", "not_fit"): 0, ("not_fit", "fit"): 0, ("not_fit", "not_fit"): 0}
    verdict_counts: dict[str, int] = {}
    disagreements: list[str] = []

    for row in rows:
        lead = leads_by_key.get(_key(row["company"]))
        # A company the run never produced a lead for still counts: as a not_fit, flagged "missing"
        verdict = lead.verdict if lead else "missing"
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        predicted = predicted_label(verdict)
        counts[(row["label"], predicted)] += 1
        if predicted != row["label"]:
            note = f" | note: {row['note']}" if row["note"].strip() else ""
            disagreements.append(f"  {row['company']}: labelled {row['label']}, tool said {verdict}{note}")

    test_keys = {_key(row["company"]) for row in rows}
    extra = [lead.company for lead in leads if _key(lead.company) not in test_keys]
    true_pos, false_pos, false_neg = counts[("fit", "fit")], counts[("not_fit", "fit")], counts[("fit", "not_fit")]
    agreed = sum(1 for row in rows if row["label_a"] == row["label_b"])

    print(f"Companies scored: {len(rows)}   Verdicts: {verdict_counts}")
    print(f"Precision: {percent(true_pos, true_pos + false_pos)}   Recall: {percent(true_pos, true_pos + false_neg)}")
    print("\nConfusion table      tool: fit   tool: not_fit")
    print(f"  labelled fit        {counts[('fit', 'fit')]:>9}   {counts[('fit', 'not_fit')]:>13}")
    print(f"  labelled not_fit    {counts[('not_fit', 'fit')]:>9}   {counts[('not_fit', 'not_fit')]:>13}")
    print(f"\nDisagreements ({len(disagreements)}):")
    print("\n".join(disagreements) if disagreements else "  none")
    print(f"\nLabeller agreement (label_a == label_b): {percent(agreed, len(rows))}")
    if extra:
        print(f"Warning: leads not in the test set, ignored: {extra}")


def main() -> None:
    """Command-line entry: score one run's leads.json against the test set."""
    parser = argparse.ArgumentParser(description="Score a run against the labelled test set.")
    parser.add_argument("leads", type=Path, help="path to a run's leads.json")
    parser.add_argument("--test-set", type=Path, default=config.TEST_SET_PATH)
    args = parser.parse_args()
    score(load_leads(args.leads), load_test_set(args.test_set))


if __name__ == "__main__":
    main()
