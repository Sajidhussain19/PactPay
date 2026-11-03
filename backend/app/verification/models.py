from pydantic import BaseModel, Field

from app.agents.contract_schema import CheckType

Measurement = float | int | str | bool | None


class Evidence(BaseModel):
    screenshots: list[str] = Field(default_factory=list, description="Paths under artifacts dir")
    logs: list[str] = Field(default_factory=list)
    measurements: dict[str, Measurement] = Field(default_factory=dict)


class CheckResult(BaseModel):
    """Outcome of one automated check.

    `passed` is None when the check could not reach a verdict (crash in our own code,
    unsupported check). Callers must treat None as "needs a human", never as a pass.
    """

    criterion_id: str
    check_type: CheckType
    passed: bool | None
    summary: str
    evidence: Evidence = Field(default_factory=Evidence)
    error: str | None = None
    duration_ms: int = 0
