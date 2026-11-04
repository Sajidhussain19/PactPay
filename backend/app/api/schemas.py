"""API response models. Internal DB records never leave the API unconverted."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.agents.contract_schema import CheckType
from app.core.config import Settings
from app.models.db import (
    ContractRecord,
    MilestoneRecord,
    User,
    VerificationRunRecord,
)
from app.paypal.payouts import split_amount
from app.services.milestones import milestone_facts
from app.workflows.milestone_state import (
    Action,
    MilestoneState,
    Role,
    allowed_actions,
)


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(id=user.id, email=user.email, name=user.name, role=user.role.value)


class CriterionOut(BaseModel):
    code: str
    description: str
    check_type: CheckType
    parameters: dict[str, Any]
    is_objective: bool
    rationale: str


class SubmissionOut(BaseModel):
    attempt: int
    live_url: str
    repo_url: str | None
    notes: str | None
    submitted_at: datetime


class RunOut(BaseModel):
    id: str
    status: str
    overall: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    error: str | None
    report: dict[str, Any] | None

    @classmethod
    def of(cls, run: VerificationRunRecord) -> "RunOut":
        return cls(
            id=run.id,
            status=run.status,
            overall=run.overall,
            started_at=run.started_at,
            finished_at=run.finished_at,
            duration_ms=run.duration_ms,
            error=run.error,
            report=run.report,
        )


class MilestoneOut(BaseModel):
    id: str
    contract_id: str
    code: str
    title: str
    amount: Decimal
    currency: str
    platform_fee: Decimal
    freelancer_receives: Decimal
    due_date: date
    state: MilestoneState
    paypal_order_id: str | None
    paypal_authorization_id: str | None
    authorization_expires_at: datetime | None
    paypal_capture_id: str | None
    payout_batch_id: str | None
    payout_status: str | None
    passed_at: datetime | None
    released_at: datetime | None
    allowed_actions: list[str]
    criteria: list[CriterionOut]
    submissions: list[SubmissionOut]
    latest_run: RunOut | None = None

    @classmethod
    def of(
        cls,
        m: MilestoneRecord,
        *,
        role: Role | None,
        settings: Settings,
        latest_run: VerificationRunRecord | None = None,
    ) -> "MilestoneOut":
        split = split_amount(m.amount, settings.platform_fee_percent)
        return cls(
            id=m.id,
            contract_id=m.contract_id,
            code=m.code,
            title=m.title,
            amount=m.amount,
            currency=m.contract.currency,
            platform_fee=split.fee,
            freelancer_receives=split.net,
            due_date=m.due_date,
            state=m.state,
            paypal_order_id=m.paypal_order_id,
            paypal_authorization_id=m.paypal_authorization_id,
            authorization_expires_at=m.authorization_expires_at,
            paypal_capture_id=m.paypal_capture_id,
            payout_batch_id=m.payout_batch_id,
            payout_status=m.payout_status,
            passed_at=m.passed_at,
            released_at=m.released_at,
            allowed_actions=ui_actions(m, role),
            criteria=[
                CriterionOut(
                    code=c.code,
                    description=c.description,
                    check_type=c.check_type,
                    parameters=c.parameters,
                    is_objective=c.is_objective,
                    rationale=c.rationale,
                )
                for c in m.criteria
            ],
            submissions=[
                SubmissionOut(
                    attempt=s.attempt,
                    live_url=s.live_url,
                    repo_url=s.repo_url,
                    notes=s.notes,
                    submitted_at=s.submitted_at,
                )
                for s in m.submissions
            ],
            latest_run=RunOut.of(latest_run) if latest_run else None,
        )


def ui_actions(m: MilestoneRecord, role: Role | None) -> list[str]:
    """Buttons this user should see. Derived from the state machine, so the UI can never
    offer an action the backend would reject (submission requires a form, so SUBMIT is
    offered by state rather than guard)."""
    if role is None:
        return []
    facts = milestone_facts(m)
    actions = {a.value for a in allowed_actions(facts, role)}
    if role is Role.FREELANCER and m.state in {MilestoneState.FUNDED, MilestoneState.FAILED}:
        actions.add(Action.SUBMIT.value)
    if role is Role.CLIENT and m.state is MilestoneState.AGREED:
        actions.add(Action.FUND.value)
    if m.state is MilestoneState.SUBMITTED:
        actions.add(Action.START_VERIFICATION.value)
    if role in {Role.CLIENT, Role.ADMIN} and m.state is MilestoneState.RELEASING:
        actions.add("retry_release")
    return sorted(actions)


class ContractOut(BaseModel):
    id: str
    title: str
    summary: str
    currency: str
    total: Decimal
    status: str
    client: UserOut
    freelancer: UserOut | None
    client_approved_at: datetime | None
    freelancer_approved_at: datetime | None
    warnings: list[Any]
    created_at: datetime
    milestones: list[MilestoneOut]

    @classmethod
    def of(cls, c: ContractRecord, *, role: Role | None, settings: Settings) -> "ContractOut":
        return cls(
            id=c.id,
            title=c.title,
            summary=c.summary,
            currency=c.currency,
            total=c.total,
            status=c.status.value,
            client=UserOut.of(c.client),
            freelancer=UserOut.of(c.freelancer) if c.freelancer else None,
            client_approved_at=c.client_approved_at,
            freelancer_approved_at=c.freelancer_approved_at,
            warnings=c.warnings,
            created_at=c.created_at,
            milestones=[MilestoneOut.of(m, role=role, settings=settings) for m in c.milestones],
        )
