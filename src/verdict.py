"""The scoring rule shared by Day 1 and Day 2: criteria results in, score and verdict out. Thresholds come from criteria.json."""

from src.schemas import CriteriaFile, CriterionResult, Met, Verdict


def _met_by_criterion(results: list[CriterionResult], criteria_file: CriteriaFile) -> dict[str, Met]:
    """Map every criterion id in the file to its answer; a criterion with no result counts as 'unknown'.

    Raises ValueError for a result whose id is not in the file, or for two results with the same id.
    """
    known_ids = [criterion.id for criterion in criteria_file.criteria]
    met_by_id: dict[str, Met] = {}
    for result in results:
        if result.criterion_id not in known_ids:
            raise ValueError(f"result for unknown criterion '{result.criterion_id}'; known ids: {known_ids}")
        if result.criterion_id in met_by_id:
            raise ValueError(f"two results for criterion '{result.criterion_id}'")
        met_by_id[result.criterion_id] = result.met

    for criterion_id in known_ids:
        # No result means nobody found evidence, which is exactly what "unknown" means
        met_by_id.setdefault(criterion_id, "unknown")
    return met_by_id


def compute_verdict(results: list[CriterionResult], criteria_file: CriteriaFile) -> tuple[int, Verdict]:
    """Apply the scoring rule from data/icp.md.

    In: the criteria results for one company, and the loaded criteria file.
    Out: (score, verdict). Score is the number of signals met. The verdict is never
    "unverified": only the pipeline sets that, after the checker rejects a card too often.
    """
    met = _met_by_criterion(results, criteria_file)

    score = 0
    for criterion in criteria_file.criteria:
        if criterion.kind == "signal" and met[criterion.id] == "yes":
            score += 1

    # 1. Proof of failure wins over everything, even over missing evidence elsewhere
    for criterion in criteria_file.criteria:
        if criterion.kind == "must_have" and met[criterion.id] == "no":
            return score, "not_fit"
        if criterion.kind == "exclusion" and met[criterion.id] == "yes":
            return score, "not_fit"

    # 2. A blocking criterion we couldn't answer means we can't decide either way
    for criterion in criteria_file.criteria:
        if criterion.unknown_blocks and met[criterion.id] == "unknown":
            return score, "not_enough_evidence"

    # 3. Every must-have is now "yes" or unknown-but-non-blocking, so only the signals decide
    if score >= criteria_file.min_signals_for_fit:
        return score, "fit"
    return score, "not_fit"
