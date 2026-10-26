"""Shared pytest fixtures: the known-good Razorpay card and its saved pages, so checker tests run offline."""

import json
from pathlib import Path

import pytest

from src.schemas import CriteriaFile, LeadCard
from src.tools.fetch import PageResult

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def good_card() -> LeadCard:
    """A real lead card from a 120b run whose quotes are all on their saved pages."""
    return LeadCard.model_validate_json((FIXTURES / "razorpay_card.json").read_text(encoding="utf-8"))


@pytest.fixture
def criteria() -> CriteriaFile:
    """A snapshot of the criteria the card was scored with, so swapping the real ICP doesn't break tests."""
    return CriteriaFile.model_validate_json((FIXTURES / "criteria.json").read_text(encoding="utf-8"))


@pytest.fixture
def saved_fetch():
    """A fetch function that serves the saved pages; any other URL is a 404. Never touches the network."""
    pages = json.loads((FIXTURES / "razorpay_pages.json").read_text(encoding="utf-8"))

    def fetch(url: str) -> PageResult:
        if url in pages:
            return PageResult(url, pages[url]["status"], pages[url]["text"], False, None)
        return PageResult(url, 404, "", True, "HTTP 404")
    return fetch


@pytest.fixture
def no_cache():
    """A read_cache function for a machine that never cached anything."""
    return lambda url: None
