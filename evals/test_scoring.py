"""pytest cases for evals/scoring.py's test-set rules: single labeller allowed, disagreements need a note."""

from pathlib import Path

import pytest

from evals.scoring import labeller_agreement, load_test_set

HEADER = "company,website,label_a,label_b,label,note\n"


def write_csv(tmp_path: Path, rows: str) -> Path:
    """Write a small test_set.csv (made-up companies) and return its path."""
    path = tmp_path / "test_set.csv"
    path.write_text(HEADER + rows, encoding="utf-8")
    return path


def test_single_labeller_loads_and_agreement_is_na(tmp_path) -> None:
    path = write_csv(tmp_path, "Acme,https://acme.example,fit,,fit,\n"
                               "Beta,https://beta.example,not_fit,,not_fit,\n")
    rows = load_test_set(path)
    assert len(rows) == 2
    assert labeller_agreement(rows) == "n/a (single labeller)"


def test_missing_label_a_is_rejected(tmp_path) -> None:
    path = write_csv(tmp_path, "Acme,https://acme.example,,,fit,\n")
    with pytest.raises(ValueError, match="label_a"):
        load_test_set(path)


def test_disagreement_without_note_is_rejected(tmp_path) -> None:
    path = write_csv(tmp_path, "Acme,https://acme.example,fit,not_fit,fit,\n")
    with pytest.raises(ValueError, match="note"):
        load_test_set(path)


def test_two_labellers_agreement_counts_only_double_labelled_rows(tmp_path) -> None:
    path = write_csv(tmp_path, "Acme,https://acme.example,fit,fit,fit,\n"
                               "Beta,https://beta.example,fit,not_fit,not_fit,settled on size\n"
                               "Gamma,https://gamma.example,fit,,fit,\n")
    assert labeller_agreement(load_test_set(path)) == "50% (1/2)"
