from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from app.agents.contract_agent import MAX_ATTEMPTS, ContractAgent
from app.agents.contract_rules import allocate_budget, vague_terms
from app.agents.contract_schema import (
    CheckParameters,
    CheckType,
    ContractRequest,
    LLMContract,
    LLMCriterion,
    LLMMilestone,
)
from tests.conftest import auth_headers

BAKERY_JOB = (
    "Landing page for my bakery: menu, contact form that emails me, works on mobile, "
    "loads fast, matches this mockup"
)


class FakeLLM:
    """Returns queued drafts in order (repeating the last) and records each call."""

    def __init__(self, *drafts: LLMContract) -> None:
        self._drafts = list(drafts)
        self.calls: list[dict[str, Any]] = []

    async def parse(
        self, *, system: str, user: str, schema: type[BaseModel], images: Sequence[bytes] = ()
    ) -> Any:
        self.calls.append({"user": user, "images": list(images)})
        index = min(len(self.calls), len(self._drafts)) - 1
        return self._drafts[index]


def criterion(
    description: str = "The /menu page shows the text 'Croissant'",
    check_type: CheckType = CheckType.ELEMENT_PRESENT,
    is_objective: bool = True,
    **params: Any,
) -> LLMCriterion:
    if check_type is CheckType.ELEMENT_PRESENT and not params:
        params = {"path": "/menu", "text": "Croissant"}
    return LLMCriterion(
        description=description,
        check_type=check_type,
        parameters=CheckParameters(**params),
        is_objective=is_objective,
        rationale="Proves the requirement.",
    )


def draft(*milestones: LLMMilestone) -> LLMContract:
    return LLMContract(
        title="Bakery landing page",
        summary="A landing page with menu and contact form.",
        milestones=list(milestones) or [milestone()],
    )


def milestone(
    amount: float = 150, due_in_days: int = 7, criteria: list[LLMCriterion] | None = None
) -> LLMMilestone:
    return LLMMilestone(
        title="Landing page",
        amount=amount,
        due_in_days=due_in_days,
        criteria=criteria if criteria is not None else [criterion()],
    )


def request(budget: str = "150", **kwargs: Any) -> ContractRequest:
    return ContractRequest(
        job_description=BAKERY_JOB, budget=Decimal(budget), start_date=date(2026, 11, 2), **kwargs
    )


async def test_clean_draft_is_normalized_in_one_call() -> None:
    llm = FakeLLM(draft())

    contract = await ContractAgent(llm).run(request())

    assert len(llm.calls) == 1
    m1 = contract.milestones[0]
    assert m1.id == "M1"
    assert m1.criteria[0].id == "M1-C1"
    assert m1.amount == Decimal("150.00")
    assert m1.due_date == date(2026, 11, 9)
    assert contract.total == Decimal("150.00")
    assert contract.warnings == []


async def test_amounts_are_rescaled_to_budget_exactly() -> None:
    llm = FakeLLM(draft(milestone(amount=60), milestone(amount=60, due_in_days=14)))

    contract = await ContractAgent(llm).run(request("100"))

    assert [m.amount for m in contract.milestones] == [Decimal("50.00"), Decimal("50.00")]
    assert contract.total == Decimal("100.00")
    assert any("rescaled" in w for w in contract.warnings)


@pytest.mark.parametrize(
    ("weights", "budget", "expected"),
    [
        ([1, 1, 1], "100", ["33.33", "33.33", "33.34"]),
        ([0, 0], "10", ["5.00", "5.00"]),
        ([3, 1], "150", ["112.50", "37.50"]),
        ([-5, 5], "20", ["0.00", "20.00"]),
    ],
)
def test_allocate_budget_always_sums_to_budget(
    weights: list[float], budget: str, expected: list[str]
) -> None:
    shares = allocate_budget(weights, Decimal(budget))
    assert shares == [Decimal(e) for e in expected]
    assert sum(shares) == Decimal(budget)


async def test_vague_criterion_triggers_repair_with_feedback() -> None:
    vague = draft(milestone(criteria=[criterion("The site looks nice and modern")]))
    fixed = draft()
    llm = FakeLLM(vague, fixed)

    contract = await ContractAgent(llm).run(request())

    assert len(llm.calls) == 2
    assert "subjective wording" in llm.calls[1]["user"]
    assert contract.milestones[0].criteria[0].check_type is CheckType.ELEMENT_PRESENT


async def test_persistent_vague_criterion_is_downgraded_to_manual() -> None:
    llm = FakeLLM(draft(milestone(criteria=[criterion("Design is beautiful")])))

    contract = await ContractAgent(llm).run(request())

    assert len(llm.calls) == MAX_ATTEMPTS
    result = contract.milestones[0].criteria[0]
    assert result.check_type is CheckType.MANUAL
    assert result.is_objective is False
    assert any("M1-C1 moved to manual" in w for w in contract.warnings)


async def test_visual_match_without_mockup_becomes_manual() -> None:
    visual = criterion("Homepage matches the mockup", CheckType.VISUAL_MATCH)
    llm = FakeLLM(draft(milestone(criteria=[visual])))

    contract = await ContractAgent(llm).run(request())

    assert contract.milestones[0].criteria[0].check_type is CheckType.MANUAL


async def test_visual_match_with_mockup_keeps_check_and_sends_image() -> None:
    visual = criterion("Homepage matches the mockup", CheckType.VISUAL_MATCH)
    llm = FakeLLM(draft(milestone(criteria=[visual])))
    mockup = b"\x89PNG fake image bytes"

    contract = await ContractAgent(llm).run(request(), mockup)

    result = contract.milestones[0].criteria[0]
    assert result.check_type is CheckType.VISUAL_MATCH
    assert result.parameters.min_match_score == 0.7
    assert llm.calls[0]["images"] == [mockup]
    assert "mockup image of the desired design is attached" in llm.calls[0]["user"]


async def test_element_present_without_target_is_rejected_then_downgraded() -> None:
    empty = LLMCriterion(
        description="Menu section exists",
        check_type=CheckType.ELEMENT_PRESENT,
        parameters=CheckParameters(),
        is_objective=True,
        rationale="r",
    )
    llm = FakeLLM(draft(milestone(criteria=[empty])))

    contract = await ContractAgent(llm).run(request())

    assert "needs a selector or text" in llm.calls[1]["user"]
    assert contract.milestones[0].criteria[0].check_type is CheckType.MANUAL


async def test_check_defaults_are_filled() -> None:
    criteria = [
        criterion("No horizontal scroll on phones and desktops", CheckType.RESPONSIVE_LAYOUT),
        criterion("Home page loads within budget", CheckType.PERFORMANCE),
    ]
    llm = FakeLLM(draft(milestone(criteria=criteria)))

    contract = await ContractAgent(llm).run(request())

    responsive, performance = contract.milestones[0].criteria
    assert responsive.parameters.viewports == [375, 1440]
    assert responsive.parameters.path == "/"
    assert performance.parameters.max_load_seconds == 3.0
    assert performance.parameters.max_page_weight_kb == 2000


async def test_due_dates_never_go_backwards() -> None:
    llm = FakeLLM(draft(milestone(due_in_days=10), milestone(due_in_days=3)))

    contract = await ContractAgent(llm).run(request())

    first, second = contract.milestones
    assert second.due_date >= first.due_date


async def test_manual_criterion_is_never_objective() -> None:
    manual = criterion("Client likes the brand feel", CheckType.MANUAL, is_objective=True)
    llm = FakeLLM(draft(milestone(criteria=[manual])))

    contract = await ContractAgent(llm).run(request())

    assert contract.milestones[0].criteria[0].is_objective is False
    assert len(llm.calls) == 1  # manual criteria may use subjective words


async def test_too_many_milestones_are_truncated() -> None:
    llm = FakeLLM(draft(*[milestone(amount=10, due_in_days=i + 1) for i in range(7)]))

    contract = await ContractAgent(llm).run(request("70"))

    assert len(contract.milestones) == 5
    assert contract.total == Decimal("70.00")
    assert any("first 5 milestones" in w for w in contract.warnings)


def test_vague_terms_matches_whole_words_only() -> None:
    assert vague_terms("A clean, Modern design") == ["clean", "modern"]
    assert vague_terms("Cleanup script runs") == []


@pytest.mark.parametrize("budget", ["0", "-5"])
def test_request_rejects_non_positive_budget(budget: str) -> None:
    with pytest.raises(ValidationError):
        ContractRequest(job_description=BAKERY_JOB, budget=Decimal(budget))


def test_draft_endpoint_without_llm_returns_503(client: TestClient) -> None:
    response = client.post(
        "/contracts/draft",
        json={"job_description": BAKERY_JOB, "budget": 150},
        headers=auth_headers(client, "client"),
    )
    assert response.status_code == 503


def test_draft_endpoint_returns_contract(client: TestClient) -> None:
    client.app.state.llm = FakeLLM(draft())

    response = client.post(
        "/contracts/draft",
        json={"job_description": BAKERY_JOB, "budget": 150},
        headers=auth_headers(client, "client"),
    )

    assert response.status_code == 200
    assert response.json()["milestones"][0]["criteria"][0]["id"] == "M1-C1"


def test_draft_endpoint_rejects_bad_mockup(client: TestClient) -> None:
    client.app.state.llm = FakeLLM(draft())

    response = client.post(
        "/contracts/draft",
        json={"job_description": BAKERY_JOB, "budget": 150, "mockup_base64": "not base64!!"},
        headers=auth_headers(client, "client"),
    )

    assert response.status_code == 422
