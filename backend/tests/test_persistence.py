import os
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.contract_schema import Contract
from app.core.db import create_engine, create_session_factory
from app.models.db import (
    AuditEvent,
    Base,
    MilestoneRecord,
    PaymentEvent,
    SubmissionRecord,
    User,
    UserRole,
)
from app.services.contracts import (
    PermissionDeniedError,
    approve_contract,
    create_contract,
    to_domain_milestone,
)
from app.services.milestones import ConcurrentUpdateError, MilestoneService
from app.services.records import SqlEventStore, record_payment_event
from app.workflows.milestone_state import Action, IllegalTransitionError, MilestoneState, Role
from tests.conftest import DEMO_DIR

BAKERY = Contract.model_validate_json((DEMO_DIR / "contracts" / "bakery.json").read_text())


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    # Set TEST_DATABASE_URL=postgresql+asyncpg://... to run these against real Postgres.
    engine = create_engine(os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest_asyncio.fixture
async def session(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s


async def users(session: AsyncSession) -> tuple[User, User]:
    client = User(email="client@pactpay.test", name="Rosie", role=UserRole.CLIENT)
    freelancer = User(
        email="dev@pactpay.test",
        name="Sam",
        role=UserRole.FREELANCER,
        paypal_email="sb-freelancer@personal.example.com",
    )
    session.add_all([client, freelancer])
    await session.commit()
    return client, freelancer


async def agreed_milestone(session: AsyncSession) -> MilestoneRecord:
    client, freelancer = await users(session)
    contract = await create_contract(session, BAKERY, client=client, freelancer=freelancer)
    await approve_contract(session, contract.id, client)
    contract = await approve_contract(session, contract.id, freelancer)
    return await MilestoneService(session).get(contract.milestones[0].id)


async def audit_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(AuditEvent)) or 0


# ------------------------------------------------------------------ contracts


async def test_contract_round_trips_to_domain_model(session: AsyncSession) -> None:
    client, _ = await users(session)

    contract = await create_contract(session, BAKERY, client=client)

    milestone = contract.milestones[0]
    assert milestone.state is MilestoneState.DRAFT
    assert milestone.amount == Decimal("150.00")
    assert to_domain_milestone(milestone) == BAKERY.milestones[0]


async def test_milestones_become_agreed_only_after_both_approve(session: AsyncSession) -> None:
    client, freelancer = await users(session)
    contract = await create_contract(session, BAKERY, client=client, freelancer=freelancer)

    contract = await approve_contract(session, contract.id, client)
    assert contract.milestones[0].state is MilestoneState.DRAFT

    contract = await approve_contract(session, contract.id, freelancer)
    assert contract.milestones[0].state is MilestoneState.AGREED


async def test_stranger_cannot_approve_contract(session: AsyncSession) -> None:
    client, _ = await users(session)
    contract = await create_contract(session, BAKERY, client=client)
    stranger = User(email="x@pactpay.test", name="X", role=UserRole.CLIENT)
    session.add(stranger)
    await session.commit()

    with pytest.raises(PermissionDeniedError):
        await approve_contract(session, contract.id, stranger)


# ----------------------------------------------------------------- milestones


async def test_transition_persists_state_updates_and_audit(session: AsyncSession) -> None:
    milestone = await agreed_milestone(session)
    before = await audit_count(session)

    await MilestoneService(session).apply(
        milestone,
        Action.FUND,
        role=Role.SYSTEM,
        actor="paypal",
        updates={"paypal_order_id": "ORDER1", "paypal_authorization_id": "AUTH1"},
    )

    reloaded = await MilestoneService(session).get(milestone.id)
    assert reloaded.state is MilestoneState.FUNDED
    assert reloaded.paypal_authorization_id == "AUTH1"
    assert await audit_count(session) == before + 1
    audit = await session.scalar(select(AuditEvent).order_by(AuditEvent.created_at.desc()))
    assert audit is not None
    assert (audit.from_state, audit.to_state, audit.actor) == ("agreed", "funded", "paypal")


async def test_illegal_transition_saves_nothing(session: AsyncSession) -> None:
    milestone = await agreed_milestone(session)
    before = await audit_count(session)

    with pytest.raises(IllegalTransitionError):
        await MilestoneService(session).apply(
            milestone, Action.FUND, role=Role.SYSTEM, actor="paypal", updates={}
        )  # no authorization id -> guard blocks

    assert milestone.state is MilestoneState.AGREED  # in-memory record untouched
    reloaded = await MilestoneService(session).get(milestone.id)
    assert reloaded.state is MilestoneState.AGREED
    assert await audit_count(session) == before


async def test_cannot_update_arbitrary_fields(session: AsyncSession) -> None:
    milestone = await agreed_milestone(session)
    with pytest.raises(ValueError, match="not updatable"):
        await MilestoneService(session).apply(
            milestone, Action.FUND, role=Role.SYSTEM, actor="x", updates={"amount": Decimal(1)}
        )


async def test_concurrent_update_is_rejected(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    milestone = await agreed_milestone(session)
    funding = {"paypal_authorization_id": "AUTH1"}

    async with session_factory() as other:
        stale = await MilestoneService(other).get(milestone.id)
        await MilestoneService(session).apply(
            milestone, Action.FUND, role=Role.SYSTEM, actor="a", updates=funding
        )
        with pytest.raises(ConcurrentUpdateError):
            await MilestoneService(other).apply(stale, Action.CANCEL, role=Role.CLIENT, actor="b")


async def test_submit_requires_a_saved_submission(session: AsyncSession) -> None:
    service = MilestoneService(session)
    milestone = await agreed_milestone(session)
    await service.apply(
        milestone,
        Action.FUND,
        role=Role.SYSTEM,
        actor="p",
        updates={"paypal_authorization_id": "A"},
    )

    with pytest.raises(IllegalTransitionError, match="no deliverable"):
        await service.apply(milestone, Action.SUBMIT, role=Role.FREELANCER, actor="dev")

    session.add(SubmissionRecord(milestone_id=milestone.id, attempt=1, live_url="https://x.dev"))
    await session.commit()
    milestone = await service.get(milestone.id)
    await service.apply(milestone, Action.SUBMIT, role=Role.FREELANCER, actor="dev")
    assert milestone.state is MilestoneState.SUBMITTED


# ------------------------------------------------------------- payment events


async def test_payment_event_is_recorded_once_per_idempotency_key(session: AsyncSession) -> None:
    first, created_first = await record_payment_event(
        session, idempotency_key="capture-m1", kind="captured", status="COMPLETED", paypal_id="C1"
    )
    second, created_second = await record_payment_event(
        session, idempotency_key="capture-m1", kind="captured", status="COMPLETED", paypal_id="C2"
    )

    assert (created_first, created_second) == (True, False)
    assert second.id == first.id
    assert await session.scalar(select(func.count()).select_from(PaymentEvent)) == 1


async def test_webhook_store_dedupes_across_sessions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = SqlEventStore(session_factory)

    assert await store.mark_processed("WH-1") is True
    assert await store.mark_processed("WH-1") is False
    assert await SqlEventStore(session_factory).mark_processed("WH-1") is False  # "after restart"
