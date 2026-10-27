"""The retry loop, offline: the LLM roles and the checker are replaced by fakes."""

from src import config, pipeline
from src.logger import RunLog
from src.schemas import CheckIssue, CheckResult
from src.llm import Usage


def _fakes(monkeypatch, good_card, verdicts: list[bool]) -> list[dict]:
    """Researcher returns the fixture evidence, drafter returns the fixture card, checker answers
    from `verdicts` in turn. Out: the arguments each drafter call received."""
    calls: list[dict] = []
    monkeypatch.setattr(pipeline, "run_researcher", lambda *args: (good_card.evidence, Usage(requests=5)))

    def fake_drafter(company, evidence, criteria_file, log, issues, rejected):
        calls.append({"evidence": [item.id for item in evidence], "issues": issues, "rejected": set(rejected)})
        return good_card, Usage(requests=1)

    def fake_check(card, criteria_file, fetch):
        if verdicts.pop(0):
            return CheckResult(passed=True, issues=[]), Usage(requests=1)
        # A different judge rejection each attempt, so a loop that forgets old ones is caught
        judged = "ai_intent" if len(calls) == 1 else "has_tech_team"
        issues = [CheckIssue(kind="quote_not_found", target_id="E2", message="E2: quote not found"),
                  CheckIssue(kind="judgement", target_id=judged, message=f"{judged}: not proven")]
        return CheckResult(passed=False, issues=issues), Usage(requests=1)

    monkeypatch.setattr(pipeline, "run_drafter", fake_drafter)
    monkeypatch.setattr(pipeline.checker, "check", fake_check)
    return calls


def test_rejected_every_time_gives_unverified(monkeypatch, tmp_path, good_card, criteria) -> None:
    calls = _fakes(monkeypatch, good_card, [False] * (1 + config.MAX_RETRIES))
    card, usage = pipeline.run_company("Razorpay", "https://razorpay.com", criteria, RunLog("t", tmp_path))
    assert card.verdict == "unverified"                   # reported, not dropped
    assert len(calls) == 1 + config.MAX_RETRIES           # first draft + 3 redrafts, no more
    assert "E2" not in calls[1]["evidence"]                # a failed record is gone for the redraft
    assert calls[1]["rejected"] == {("ai_intent", "E4")}   # the judged card cited E4 for ai_intent
    assert calls[2]["rejected"] == {("ai_intent", "E4"), ("has_tech_team", "E3")}  # rejections pile up
    assert usage.requests == 5 + 2 * (1 + config.MAX_RETRIES)  # every role's cost is counted


def test_accepted_on_retry_keeps_the_real_verdict(monkeypatch, tmp_path, good_card, criteria) -> None:
    calls = _fakes(monkeypatch, good_card, [False, True])
    card, _ = pipeline.run_company("Razorpay", "https://razorpay.com", criteria, RunLog("t", tmp_path))
    assert card.verdict == good_card.verdict
    assert len(calls) == 2
    assert calls[0]["issues"] is None and calls[1]["issues"]  # feedback only on the retry
