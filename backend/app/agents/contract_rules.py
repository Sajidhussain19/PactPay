"""Deterministic contract validation and normalization.

The model drafts; code decides. Anything money- or trust-related is enforced here:
budget allocation, due dates, ids, and whether a criterion is really machine-checkable.
"""

import re
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

from app.agents.contract_schema import (
    CheckParameters,
    CheckType,
    Contract,
    ContractRequest,
    Criterion,
    LLMContract,
    LLMCriterion,
    Milestone,
)
from app.core.money import CENT, to_money

MAX_MILESTONES = 5
MAX_CRITERIA_PER_MILESTONE = 10

VAGUE_TERMS = (
    "nice",
    "beautiful",
    "modern",
    "clean",
    "professional",
    "attractive",
    "user-friendly",
    "user friendly",
    "intuitive",
    "high quality",
    "high-quality",
    "pretty",
    "sleek",
    "elegant",
    "looks good",
    "appealing",
    "polished",
)
_VAGUE_PATTERN = re.compile(r"\b(" + "|".join(re.escape(t) for t in VAGUE_TERMS) + r")\b", re.I)

DEFAULT_VIEWPORTS = [375, 1440]
DEFAULT_MAX_LOAD_SECONDS = 3.0
DEFAULT_MAX_PAGE_WEIGHT_KB = 2000
DEFAULT_MIN_MATCH_SCORE = 0.7


def vague_terms(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in _VAGUE_PATTERN.finditer(text)})


def missing_parameters(criterion: LLMCriterion, *, has_mockup: bool) -> str | None:
    """Return why an objective check can't run as specified, or None if it can."""
    params = criterion.parameters
    match criterion.check_type:
        case CheckType.ELEMENT_PRESENT if not (params.selector or params.text):
            return "element_present needs a selector or text"
        case CheckType.VISUAL_MATCH if not has_mockup:
            return "visual_match needs a mockup image, and none was provided"
        case _:
            return None


def find_issues(draft: LLMContract, request: ContractRequest) -> list[str]:
    """Problems worth sending back to the model for a repair attempt."""
    issues: list[str] = []
    if not draft.milestones:
        issues.append("The contract has no milestones.")
    if len(draft.milestones) > MAX_MILESTONES:
        issues.append(f"Use at most {MAX_MILESTONES} milestones.")
    for m_index, milestone in enumerate(draft.milestones, start=1):
        label = f"Milestone {m_index} ({milestone.title!r})"
        if not milestone.criteria:
            issues.append(f"{label} has no acceptance criteria.")
        if len(milestone.criteria) > MAX_CRITERIA_PER_MILESTONE:
            issues.append(f"{label} has more than {MAX_CRITERIA_PER_MILESTONE} criteria.")
        for c_index, criterion in enumerate(milestone.criteria, start=1):
            c_label = f"{label}, criterion {c_index} ({criterion.description!r})"
            if not criterion.is_objective or criterion.check_type is CheckType.MANUAL:
                continue
            if terms := vague_terms(criterion.description):
                issues.append(
                    f"{c_label} uses subjective wording {terms}. Rewrite it as something a "
                    "program can measure, or set check_type=manual and is_objective=false."
                )
            if reason := missing_parameters(criterion, has_mockup=request.has_mockup):
                issues.append(f"{c_label}: {reason}.")
    return issues


def allocate_budget(weights: list[float], budget: Decimal) -> list[Decimal]:
    """Split `budget` in proportion to the model's amounts, exact to the cent.

    Model arithmetic is not trusted: amounts are treated as weights, and rounding
    leftovers go to the last milestone so the parts always sum to the budget.
    """
    budget = to_money(budget)
    clean = [max(Decimal(str(w)), Decimal(0)) for w in weights]
    total = sum(clean)
    if total == 0:
        clean = [Decimal(1)] * len(weights)
        total = Decimal(len(weights))
    shares = [(budget * w / total).quantize(CENT, rounding=ROUND_DOWN) for w in clean]
    shares[-1] += budget - sum(shares)
    return shares


def _with_defaults(check_type: CheckType, params: CheckParameters) -> CheckParameters:
    updates: dict[str, object] = {}
    if (
        check_type
        in {
            CheckType.PAGE_LOADS,
            CheckType.ELEMENT_PRESENT,
            CheckType.FORM_SUBMITS,
            CheckType.RESPONSIVE_LAYOUT,
            CheckType.PERFORMANCE,
            CheckType.VISUAL_MATCH,
        }
        and not params.path
    ):
        updates["path"] = "/"
    if check_type is CheckType.RESPONSIVE_LAYOUT and not params.viewports:
        updates["viewports"] = DEFAULT_VIEWPORTS
    if check_type is CheckType.PERFORMANCE:
        if params.max_load_seconds is None:
            updates["max_load_seconds"] = DEFAULT_MAX_LOAD_SECONDS
        if params.max_page_weight_kb is None:
            updates["max_page_weight_kb"] = DEFAULT_MAX_PAGE_WEIGHT_KB
    if check_type is CheckType.VISUAL_MATCH and params.min_match_score is None:
        updates["min_match_score"] = DEFAULT_MIN_MATCH_SCORE
    return params.model_copy(update=updates)


def _normalize_criterion(
    criterion: LLMCriterion, criterion_id: str, request: ContractRequest, warnings: list[str]
) -> Criterion:
    check_type = criterion.check_type
    is_objective = criterion.is_objective and check_type is not CheckType.MANUAL

    downgrade_reason = None
    if is_objective:
        if terms := vague_terms(criterion.description):
            downgrade_reason = f"subjective wording {terms}"
        elif reason := missing_parameters(criterion, has_mockup=request.has_mockup):
            downgrade_reason = reason
    if downgrade_reason:
        warnings.append(f"{criterion_id} moved to manual review: {downgrade_reason}.")
        check_type, is_objective = CheckType.MANUAL, False
    if check_type is CheckType.MANUAL:
        is_objective = False

    return Criterion(
        id=criterion_id,
        description=criterion.description.strip(),
        check_type=check_type,
        parameters=_with_defaults(check_type, criterion.parameters),
        is_objective=is_objective,
        rationale=criterion.rationale.strip(),
    )


def normalize(draft: LLMContract, request: ContractRequest) -> Contract:
    """Turn a model draft into a contract PactPay can enforce. Never raises on model quirks;
    anything it fixes is recorded in `warnings` so both parties see it before agreeing."""
    if not draft.milestones:
        raise ValueError("Cannot normalize a contract with no milestones")

    warnings: list[str] = []
    milestones_in = draft.milestones[:MAX_MILESTONES]
    if len(draft.milestones) > MAX_MILESTONES:
        warnings.append(f"Kept the first {MAX_MILESTONES} milestones.")

    requested = sum(Decimal(str(m.amount)) for m in milestones_in)
    amounts = allocate_budget([m.amount for m in milestones_in], request.budget)
    if abs(requested - request.budget) > CENT:
        warnings.append(
            f"Milestone amounts were rescaled to match the budget of {request.budget} "
            f"{request.currency} (draft summed to {requested})."
        )

    start = request.start_date or date.today()
    milestones: list[Milestone] = []
    previous_days = 0
    for m_index, (raw, amount) in enumerate(zip(milestones_in, amounts, strict=True), start=1):
        days = max(raw.due_in_days, previous_days, 1)  # due dates never go backwards
        previous_days = days
        milestone_id = f"M{m_index}"
        criteria = [
            _normalize_criterion(c, f"{milestone_id}-C{c_index}", request, warnings)
            for c_index, c in enumerate(raw.criteria[:MAX_CRITERIA_PER_MILESTONE], start=1)
        ]
        if not criteria:
            warnings.append(f"{milestone_id} has no criteria; it will need manual approval.")
        milestones.append(
            Milestone(
                id=milestone_id,
                title=raw.title.strip(),
                amount=amount,
                due_date=start + timedelta(days=days),
                criteria=criteria,
            )
        )

    return Contract(
        title=draft.title.strip(),
        summary=draft.summary.strip(),
        currency=request.currency,
        total=sum((m.amount for m in milestones), Decimal(0)),
        milestones=milestones,
        warnings=warnings,
    )
