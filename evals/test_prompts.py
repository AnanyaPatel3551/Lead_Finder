"""Fairness guard: the Day 1 and Day 2 research prompts must share their evidence-quality rules word for word."""

from src.agents import researcher, single_agent

CONCRETE_LINE = "- Prefer concrete evidence (job roles, product features, stated numbers) over slogans and taglines."


def _quote_rules(prompt: str) -> str:
    """The prompt's "Quote rules:" block: from its heading to the end of the prompt."""
    assert "Quote rules:" in prompt, "the prompt has no 'Quote rules:' block"
    return prompt[prompt.index("Quote rules:"):]


def test_quote_rules_are_identical() -> None:
    """A difference here would mean the two architectures were told different things about quotes."""
    assert _quote_rules(researcher.SYSTEM_PROMPT) == _quote_rules(single_agent.SYSTEM_PROMPT)


def test_both_prompts_prefer_concrete_evidence() -> None:
    """The line that steadies the judge on slogan quotes must be in both prompts, unchanged."""
    assert CONCRETE_LINE in single_agent.SYSTEM_PROMPT.splitlines()
    assert CONCRETE_LINE in researcher.SYSTEM_PROMPT.splitlines()
