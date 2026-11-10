"""Judge demo mode: seeded users, a ready-to-fund contract, and a one-click reset."""

from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contract_schema import (
    CheckParameters,
    CheckType,
    Contract,
    Criterion,
    Milestone,
)
from app.core.config import Settings
from app.models.db import Base, ContractRecord, User, UserRole, utcnow
from app.services.contracts import approve_contract, create_contract

DEMO_USERS = {
    UserRole.CLIENT: ("client@pactpay.demo", "Rosie (demo client)"),
    UserRole.FREELANCER: ("freelancer@pactpay.demo", "Sam (demo freelancer)"),
    UserRole.ADMIN: ("admin@pactpay.demo", "PactPay reviewer (demo admin)"),
}


async def ensure_demo_users(session: AsyncSession, settings: Settings) -> dict[UserRole, User]:
    users: dict[UserRole, User] = {}
    for role, (email, name) in DEMO_USERS.items():
        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, name=name, role=role)
            session.add(user)
        if role is UserRole.FREELANCER:
            user.paypal_email = settings.demo_freelancer_paypal_email or None
        users[role] = user
    await session.commit()
    return users


def _cafe_contract() -> Contract:
    """A second, smaller contract still waiting for the freelancer's approval."""
    criteria = [
        Criterion(
            id="M1-C1",
            description="The menu page loads with HTTP 200 and no JavaScript errors.",
            check_type=CheckType.PAGE_LOADS,
            parameters=CheckParameters(path="/"),
            is_objective=True,
            rationale="A broken page can't be used.",
        ),
        Criterion(
            id="M1-C2",
            description="The menu lists 'Espresso'.",
            check_type=CheckType.ELEMENT_PRESENT,
            parameters=CheckParameters(path="/", text="Espresso"),
            is_objective=True,
            rationale="The client asked for the coffee menu.",
        ),
        Criterion(
            id="M1-C3",
            description="No horizontal scrolling on phones (375px).",
            check_type=CheckType.RESPONSIVE_LAYOUT,
            parameters=CheckParameters(path="/", viewports=[375, 1440]),
            is_objective=True,
            rationale="Most customers read the menu on a phone.",
        ),
    ]
    return Contract(
        title="Corner Café menu page refresh",
        summary="Refresh the drinks menu page and make it readable on phones.",
        currency="USD",
        total=Decimal("80.00"),
        milestones=[
            Milestone(
                id="M1",
                title="Menu page refreshed",
                amount=Decimal("80.00"),
                due_date=utcnow().date(),
                criteria=criteria,
            )
        ],
    )


async def reset_demo(session: AsyncSession, settings: Settings) -> dict[str, str]:
    """Wipe all demo data and seed a fresh, ready-to-fund bakery contract."""
    for table in reversed(Base.metadata.sorted_tables):
        if table.name != "users":
            await session.execute(delete(table))
    await session.commit()
    users = await ensure_demo_users(session, settings)
    client, freelancer = users[UserRole.CLIENT], users[UserRole.FREELANCER]

    bakery = Contract.model_validate_json(
        (settings.demo_dir / "contracts" / "bakery.json").read_text(encoding="utf-8")
    )
    mockup = settings.demo_dir / "mockup" / "mockup.png"
    contract = await create_contract(
        session,
        bakery,
        client=client,
        freelancer=freelancer,
        mockup_path=str(mockup) if mockup.exists() else None,
    )
    await approve_contract(session, contract.id, client)
    contract = await approve_contract(session, contract.id, freelancer)

    cafe = await create_contract(session, _cafe_contract(), client=client, freelancer=freelancer)
    await approve_contract(session, cafe.id, client)

    return {"contract_id": contract.id, "milestone_id": contract.milestones[0].id}


async def seed_if_empty(session: AsyncSession, settings: Settings) -> None:
    if not await session.scalar(select(func.count()).select_from(ContractRecord)):
        await reset_demo(session, settings)
