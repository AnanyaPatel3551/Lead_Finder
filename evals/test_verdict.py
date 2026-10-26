"""pytest cases for src/verdict.py: one per verdict, plus the precedence and unknown_blocks rules."""

import pytest

from src.schemas import CriteriaFile, CriterionResult
from src.verdict import compute_verdict

# Made-up criteria: tests check the RULE, so they must not contain real ICP content
CRITERIA = CriteriaFile.model_validate({
    "version": "test",
    "min_signals_for_fit": 2,
    "criteria": [
        {"id": "must_a", "kind": "must_have", "description": "a", "unknown_blocks": True},
        {"id": "must_soft", "kind": "must_have", "description": "b", "unknown_blocks": False},
        {"id": "signal_a", "kind": "signal", "description": "c", "unknown_blocks": False},
        {"id": "signal_b", "kind": "signal", "description": "d", "unknown_blocks": False},
        {"id": "excl_a", "kind": "exclusion", "description": "e", "unknown_blocks": True},
    ],
})

GOOD = {"must_a": "yes", "must_soft": "yes", "signal_a": "yes", "signal_b": "yes", "excl_a": "no"}


def results(answers: dict[str, str]) -> list[CriterionResult]:
    """Build CriterionResults; yes/no answers get a dummy evidence id, as the schema requires."""
    return [CriterionResult(criterion_id=cid, met=met, evidence_id=None if met == "unknown" else "E1")
            for cid, met in answers.items()]


def test_fit_when_all_met() -> None:
    assert compute_verdict(results(GOOD), CRITERIA) == (2, "fit")


def test_not_fit_when_must_have_fails() -> None:
    assert compute_verdict(results({**GOOD, "must_a": "no"}), CRITERIA) == (2, "not_fit")


def test_not_fit_when_exclusion_triggered() -> None:
    assert compute_verdict(results({**GOOD, "excl_a": "yes"}), CRITERIA) == (2, "not_fit")


def test_not_fit_when_too_few_signals() -> None:
    assert compute_verdict(results({**GOOD, "signal_b": "unknown"}), CRITERIA) == (1, "not_fit")


def test_not_enough_evidence_when_blocking_unknown() -> None:
    assert compute_verdict(results({**GOOD, "excl_a": "unknown"}), CRITERIA) == (2, "not_enough_evidence")


def test_unknown_non_blocking_must_have_still_fits() -> None:
    assert compute_verdict(results({**GOOD, "must_soft": "unknown"}), CRITERIA) == (2, "fit")


def test_proof_of_failure_beats_unknown() -> None:
    answers = {**GOOD, "must_a": "unknown", "excl_a": "yes"}
    assert compute_verdict(results(answers), CRITERIA) == (2, "not_fit")


def test_missing_result_counts_as_unknown() -> None:
    answers = {cid: met for cid, met in GOOD.items() if cid != "must_a"}
    assert compute_verdict(results(answers), CRITERIA) == (2, "not_enough_evidence")


def test_unknown_criterion_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown criterion"):
        compute_verdict(results({**GOOD, "made_up": "yes"}), CRITERIA)
