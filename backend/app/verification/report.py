from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.agents.contract_schema import CheckType
from app.verification.models import CheckResult


class Submission(BaseModel):
    live_url: str
    repo_url: str | None = None

    @field_validator("live_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("live_url must be an http(s) URL")
        return value


class Verdict(StrEnum):
    PASS = "pass"  # noqa: S105 - not a password
    FAIL = "fail"
    NEEDS_REVIEW = "needs_review"  # automated verdict is uncertain; a human decides
    MANUAL = "manual"  # subjective criterion; client confirms when approving release


class OverallVerdict(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class CriterionVerdict(BaseModel):
    criterion_id: str
    description: str
    check_type: CheckType
    verdict: Verdict
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    decided_by: Literal["judge", "rules", "human"]
    fix_suggestion: str | None = None
    check: CheckResult | None = None


class VerificationReport(BaseModel):
    milestone_id: str
    submission: Submission
    overall: OverallVerdict
    criteria: list[CriterionVerdict]
    counts: dict[str, int]
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    security_flags: list[str] = Field(default_factory=list)

    @property
    def fixes(self) -> list[str]:
        return [
            f"{c.criterion_id}: {c.fix_suggestion}"
            for c in self.criteria
            if c.verdict is Verdict.FAIL and c.fix_suggestion
        ]


class VerificationEvent(BaseModel):
    """Progress events for the live UI (streamed over SSE)."""

    type: Literal["started", "check_started", "check_finished", "judged", "report_ready"]
    criterion_id: str | None = None
    check_type: CheckType | None = None
    status: str | None = None
    summary: str | None = None
    screenshot: str | None = None


def overall_verdict(verdicts: list[CriterionVerdict]) -> OverallVerdict:
    states = {v.verdict for v in verdicts}
    if Verdict.FAIL in states:
        return OverallVerdict.FAILED
    if Verdict.NEEDS_REVIEW in states or not states - {Verdict.MANUAL}:
        # Nothing was verified automatically -> a human must look.
        return OverallVerdict.NEEDS_REVIEW
    return OverallVerdict.PASSED
