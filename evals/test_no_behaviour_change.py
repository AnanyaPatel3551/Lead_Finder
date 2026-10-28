"""The two cost-only changes: the retry log in llm.py and the judge skip in checker.py."""

from pydantic import BaseModel

from src import llm
from src.agents import checker
from src.schemas import CriterionResult


class _Reply(BaseModel):
    """A tiny schema for faking call_llm_validated's two attempts."""

    answer: str


def test_first_attempt_error_is_recorded_and_retry_unchanged(monkeypatch) -> None:
    """The first error is recorded once; the caller still gets the second attempt, usage summed."""
    replies = [llm.LLMResponse(None, None, "bad json", "json_validate_failed", llm.Usage(requests=1)),
               llm.LLMResponse(None, _Reply(answer="ok"), None, "stop", llm.Usage(requests=1, input_tokens=10))]
    monkeypatch.setattr(llm, "call_llm", lambda *args, **kwargs: replies.pop(0))
    llm.take_first_attempt_errors()  # start empty
    response = llm.call_llm_validated([{"role": "user", "content": "hi"}], _Reply)
    assert response.parsed.answer == "ok" and response.usage.requests == 2
    errors = llm.take_first_attempt_errors()
    assert [(e["schema"], e["finish_reason"], e["error"]) for e in errors] == [("_Reply", "json_validate_failed", "bad json")]
    assert llm.take_first_attempt_errors() == []  # taking clears them


def test_judge_skipped_only_when_card_has_no_claims(monkeypatch, good_card, criteria, saved_fetch, no_cache) -> None:
    """An all-unknown card makes no judge call; a card with claims still gets judged."""
    judged = []

    def fake_judge(card, criteria_file):
        judged.append(card.company)
        return [], llm.Usage(requests=1)

    monkeypatch.setattr(checker, "judge_lead", fake_judge)
    empty = good_card.model_copy(update={
        "criteria": [CriterionResult(criterion_id=c.id, met="unknown", evidence_id=None) for c in criteria.criteria],
        "evidence": [], "score": 0, "verdict": "not_enough_evidence"})
    result, usage = checker.check(empty, criteria, fetch=saved_fetch, read_cache=no_cache)
    assert result.passed and judged == [] and usage.requests == 0
    checker.check(good_card, criteria, fetch=saved_fetch, read_cache=no_cache)
    assert judged == ["Razorpay"]
