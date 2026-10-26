"""Pydantic models that every handoff uses: the contract between teammates' code."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Allowed values live here once, so a typo like "not-fit" fails validation instead of scoring wrong
Verdict = Literal["fit", "not_fit", "not_enough_evidence", "unverified"]
Met = Literal["yes", "no", "unknown"]
CriterionKind = Literal["must_have", "signal", "exclusion"]
IssueKind = Literal["url_unreachable", "quote_not_found", "unknown_evidence_id",
                    "score_mismatch", "personal_data", "judgement"]


class Contract(BaseModel):
    """Base for every schema: an unknown key is an error, never silently dropped."""

    model_config = ConfigDict(extra="forbid")


class Criterion(Contract):
    """One ICP criterion. The real ones come only from data/criteria.json, never from code."""

    id: str = Field(min_length=1)
    kind: CriterionKind
    description: str = Field(min_length=1)
    unknown_blocks: bool  # true: if still unknown after research, the verdict is not_enough_evidence

    @model_validator(mode="after")
    def signals_never_block(self) -> "Criterion":
        """An unknown signal simply doesn't count, so unknown_blocks=true on one would mislead."""
        if self.kind == "signal" and self.unknown_blocks:
            raise ValueError(f"criterion '{self.id}' is a signal: unknown_blocks must be false")
        return self


class CriteriaFile(Contract):
    """The whole of data/criteria.json: swapping in the real ICP means replacing this file only."""

    version: str = Field(min_length=1)  # logged with every run, to prove both days used the same file
    min_signals_for_fit: int = Field(ge=0)
    criteria: list[Criterion] = Field(min_length=1)


class Evidence(Contract):
    """One fact the researcher found, with the exact quote and the page it came from."""

    id: str = Field(min_length=1)
    criterion_id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    source_url: str
    quote: str = Field(min_length=1)

    @field_validator("source_url")
    @classmethod
    def url_must_be_web_address(cls, value: str) -> str:
        """Reject sources the checker could never load, like 'company website' or 'acme.com'."""
        if not value.startswith(("http://", "https://")):
            raise ValueError("source_url must be a full URL starting with http:// or https://")
        return value


class CriterionResult(Contract):
    """The decision on one criterion, pointing at the evidence that supports it."""

    criterion_id: str = Field(min_length=1)
    met: Met
    evidence_id: str | None

    @model_validator(mode="after")
    def decision_needs_evidence(self) -> "CriterionResult":
        """A 'yes' or 'no' must cite evidence; without evidence the honest answer is 'unknown'."""
        if self.met != "unknown" and self.evidence_id is None:
            raise ValueError(
                f"criterion '{self.criterion_id}' is '{self.met}' but has no evidence_id: "
                "cite an evidence id, or set met to 'unknown'"
            )
        return self


class LeadCard(Contract):
    """The final answer for one company: what a human reviewer reads."""

    company: str = Field(min_length=1)
    verdict: Verdict
    score: int = Field(ge=0)  # computed by Python from the criteria results, not by the LLM
    criteria: list[CriterionResult]
    evidence: list[Evidence]  # the cited records travel with the card, so it can be checked alone
    why_it_fits: str


class CheckIssue(Contract):
    """One problem the checker found, specific enough for the drafter to fix."""

    kind: IssueKind
    target_id: str | None  # the evidence or criterion id the problem is about, if any
    message: str = Field(min_length=1)


class CheckResult(Contract):
    """The checker's decision on one lead card; issues go back to the drafter on rejection."""

    passed: bool
    issues: list[CheckIssue]

    @model_validator(mode="after")
    def passed_means_no_issues(self) -> "CheckResult":
        """Stop the contradictory 'passed, but here are 3 issues' (or 'failed, no reason')."""
        if self.passed == bool(self.issues):
            raise ValueError("passed must be true exactly when issues is empty")
        return self
