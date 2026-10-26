"""pytest tests that plant known errors (fake quote, dead URL, wrong score) and expect the checker to catch them.

Each test injects ONE error into the known-good card and asserts the checker rejects it for the
RIGHT reason (issue kind and target), not merely that it rejects something.
"""

import pytest

from src.agents.checker import check, check_lead
from src.verdict import compute_verdict


def _show(name: str, issues: list) -> None:
    """Print the issues, so the saved test output shows WHY each card was rejected."""
    print(f"\n[{name}] {len(issues)} issue(s)")
    for issue in issues:
        print(f"  {issue.kind} ({issue.target_id}): {issue.message}")


def test_good_fixture_passes_code_checks(good_card, criteria, saved_fetch, no_cache):
    """Without this, a checker that rejects everything would pass every test below."""
    issues = check_lead(good_card, criteria, fetch=saved_fetch, read_cache=no_cache)
    _show("good fixture", issues)
    assert issues == []


def test_rejects_made_up_url(good_card, criteria, saved_fetch, no_cache):
    card = good_card.model_copy(deep=True)
    card.evidence[0].source_url = "https://razorpay.com/about-our-ai-lab/"  # a page that doesn't exist
    issues = check_lead(card, criteria, fetch=saved_fetch, read_cache=no_cache)
    _show("made-up URL", issues)
    assert [(i.kind, i.target_id) for i in issues] == [("url_unreachable", card.evidence[0].id)]


def test_rejects_quote_not_on_page(good_card, criteria, saved_fetch):
    card = good_card.model_copy(deep=True)
    card.evidence[3].quote = "Razorpay is hiring 50 machine learning engineers this year"
    # The cached copy is the same saved page, so the checker can tell this quote was made up
    issues = check_lead(card, criteria, fetch=saved_fetch, read_cache=saved_fetch)
    _show("quote not on page", issues)
    assert [(i.kind, i.target_id) for i in issues] == [("quote_not_found", card.evidence[3].id)]
    assert "likely made up" in issues[0].message


@pytest.mark.llm
def test_rejects_non_icp_company(good_card, criteria, saved_fetch, no_cache):
    """Only the judge can catch this: the quote is real and the score adds up, but 5,233 is out of range."""
    card = good_card.model_copy(deep=True)
    size = next(r for r in card.criteria if r.criterion_id == "size")
    size.met = "yes"  # the one planted error: claim a company of ~5,233 employees is within 50 to 5,000
    card.score, card.verdict = compute_verdict(card.criteria, criteria)  # keep the card internally consistent
    assert check_lead(card, criteria, fetch=saved_fetch, read_cache=no_cache) == []  # code checks can't see it

    result, usage = check(card, criteria, fetch=saved_fetch, read_cache=no_cache)
    _show(f"non-ICP company (judge, {usage.requests} request, {usage.input_tokens + usage.output_tokens} tokens)",
          result.issues)
    assert not result.passed
    assert ("judgement", "size") in [(i.kind, i.target_id) for i in result.issues]
