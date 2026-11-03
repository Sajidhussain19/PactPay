"""Contract data shapes.

Two layers:
- `LLM*` models are what the model is asked to produce (strict JSON schema, no free-form dicts).
- `Contract`, `Milestone`, `Criterion` are the validated, normalized result the rest of
  PactPay trusts: ids assigned, money as Decimal, due dates computed by code, not the model.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class CheckType(StrEnum):
    PAGE_LOADS = "page_loads"
    ELEMENT_PRESENT = "element_present"
    FORM_SUBMITS = "form_submits"
    RESPONSIVE_LAYOUT = "responsive_layout"
    PERFORMANCE = "performance"
    VISUAL_MATCH = "visual_match"
    REPO_CHECK = "repo_check"
    TESTS_PASS = "tests_pass"
    MANUAL = "manual"


class CheckParameters(BaseModel):
    """Flat parameter bag; each check type reads the fields it needs (see contract_rules)."""

    path: str | None = Field(None, description="Page path on the live site, e.g. '/' or '/menu'")
    selector: str | None = Field(None, description="CSS selector that must exist")
    text: str | None = Field(None, description="Visible text that must appear on the page")
    success_text: str | None = Field(
        None, description="Text shown after a successful form submission, if known"
    )
    viewports: list[int] | None = Field(None, description="Viewport widths in px to test")
    max_load_seconds: float | None = None
    max_page_weight_kb: int | None = None
    min_match_score: float | None = Field(None, description="0-1 similarity for visual_match")
    required_files: list[str] | None = Field(None, description="Repo files that must exist")


# ---------------------------------------------------------------- model output


class LLMCriterion(BaseModel):
    description: str = Field(description="One objectively checkable requirement")
    check_type: CheckType
    parameters: CheckParameters
    is_objective: bool
    rationale: str = Field(description="Why this check proves the requirement (one sentence)")


class LLMMilestone(BaseModel):
    title: str
    amount: float = Field(description="Share of the budget for this milestone")
    due_in_days: int = Field(description="Days from contract start until this milestone is due")
    criteria: list[LLMCriterion]


class LLMContract(BaseModel):
    title: str
    summary: str
    milestones: list[LLMMilestone]


# ------------------------------------------------------------- normalized result


class ContractRequest(BaseModel):
    job_description: str = Field(min_length=20, max_length=5000)
    budget: Decimal = Field(gt=0, le=Decimal("100000"))
    currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    start_date: date | None = None
    has_mockup: bool = False


class Criterion(BaseModel):
    id: str
    description: str
    check_type: CheckType
    parameters: CheckParameters
    is_objective: bool
    rationale: str


class Milestone(BaseModel):
    id: str
    title: str
    amount: Decimal
    due_date: date
    criteria: list[Criterion]


class Contract(BaseModel):
    title: str
    summary: str
    currency: str
    total: Decimal
    milestones: list[Milestone]
    warnings: list[str] = []
