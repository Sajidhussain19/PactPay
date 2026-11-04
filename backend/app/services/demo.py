"""Seeded demo users so judges can log in with one click."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.db import User, UserRole

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
