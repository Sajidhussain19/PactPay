"""Persist contracts and convert between DB records and domain models."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.contract_schema import CheckParameters, Contract, Criterion, Milestone
from app.models.db import (
    AuditEvent,
    ContractRecord,
    ContractStatus,
    CriterionRecord,
    MilestoneRecord,
    User,
    UserRole,
    utcnow,
)
from app.services.milestones import MilestoneService, NotFoundError
from app.workflows.milestone_state import Action, MilestoneState, Role


class PermissionDeniedError(Exception):
    pass


async def create_contract(
    session: AsyncSession,
    draft: Contract,
    *,
    client: User,
    freelancer: User | None = None,
    mockup_path: str | None = None,
) -> ContractRecord:
    contract = ContractRecord(
        title=draft.title,
        summary=draft.summary,
        currency=draft.currency,
        total=draft.total,
        client_id=client.id,
        freelancer_id=freelancer.id if freelancer else None,
        mockup_path=mockup_path,
        warnings=list(draft.warnings),
    )
    for m_pos, milestone in enumerate(draft.milestones):
        record = MilestoneRecord(
            code=milestone.id,
            position=m_pos,
            title=milestone.title,
            amount=milestone.amount,
            due_date=milestone.due_date,
            state=MilestoneState.DRAFT,
        )
        record.criteria = [
            CriterionRecord(
                code=c.id,
                position=c_pos,
                description=c.description,
                check_type=c.check_type,
                parameters=c.parameters.model_dump(exclude_none=True),
                is_objective=c.is_objective,
                rationale=c.rationale,
            )
            for c_pos, c in enumerate(milestone.criteria)
        ]
        contract.milestones.append(record)
    session.add(contract)
    session.add(AuditEvent(contract_id=contract.id, actor=client.id, action="contract_created"))
    await session.commit()
    return await get_contract(session, contract.id)


async def get_contract(session: AsyncSession, contract_id: str) -> ContractRecord:
    contract = await session.scalar(
        select(ContractRecord)
        .where(ContractRecord.id == contract_id)
        .options(
            selectinload(ContractRecord.milestones).selectinload(MilestoneRecord.criteria),
            selectinload(ContractRecord.milestones).selectinload(MilestoneRecord.submissions),
            selectinload(ContractRecord.client),
            selectinload(ContractRecord.freelancer),
        )
    )
    if contract is None:
        raise NotFoundError(f"Contract {contract_id} not found")
    return contract


async def approve_contract(session: AsyncSession, contract_id: str, user: User) -> ContractRecord:
    """Record one party's approval; once both approved, every milestone becomes AGREED."""
    contract = await get_contract(session, contract_id)
    if contract.status is not ContractStatus.DRAFT:
        raise PermissionDeniedError(f"Contract is {contract.status}, not draft")
    if user.id == contract.client_id:
        contract.client_approved_at = contract.client_approved_at or utcnow()
    elif user.role is UserRole.FREELANCER and contract.freelancer_id in (None, user.id):
        contract.freelancer_id = user.id
        contract.freelancer_approved_at = contract.freelancer_approved_at or utcnow()
    else:
        raise PermissionDeniedError("Only this contract's client or freelancer can approve it")
    session.add(AuditEvent(contract_id=contract.id, actor=user.id, action="contract_approved"))
    await session.commit()

    if contract.is_agreed:
        contract.status = ContractStatus.AGREED
        await session.commit()
        service = MilestoneService(session)
        for milestone in contract.milestones:
            await service.apply(milestone, Action.AGREE, role=Role.SYSTEM, actor="system")
    return await get_contract(session, contract_id)


def to_domain_milestone(record: MilestoneRecord) -> Milestone:
    """Build the immutable Milestone the verification workflow runs on."""
    return Milestone(
        id=record.code,
        title=record.title,
        amount=record.amount,
        due_date=record.due_date,
        criteria=[
            Criterion(
                id=c.code,
                description=c.description,
                check_type=c.check_type,
                parameters=CheckParameters.model_validate(c.parameters),
                is_objective=c.is_objective,
                rationale=c.rationale,
            )
            for c in record.criteria
        ],
    )
