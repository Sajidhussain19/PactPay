from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import InvalidTokenError, verify_token
from app.models.db import ContractRecord, User, UserRole
from app.services.payments import PaymentRuntime, PaymentService
from app.services.verification_manager import VerificationManager
from app.workflows.milestone_state import Role


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def current_user(
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Log in first")
    try:
        user_id = verify_token(
            authorization.removeprefix("Bearer "), settings.secret_key.get_secret_value()
        )
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown user")
    return user


UserDep = Annotated[User, Depends(current_user)]


def payments(request: Request, session: SessionDep) -> PaymentService:
    runtime: PaymentRuntime = request.app.state.payments
    return runtime.service(session)


def verifier(request: Request) -> VerificationManager:
    return request.app.state.verifier


PaymentsDep = Annotated[PaymentService, Depends(payments)]
VerifierDep = Annotated[VerificationManager, Depends(verifier)]


def role_in_contract(contract: ContractRecord, user: User) -> Role | None:
    """The state-machine role this user plays on this contract, if any."""
    if user.role is UserRole.ADMIN:
        return Role.ADMIN
    if contract.client_id == user.id:
        return Role.CLIENT
    if contract.freelancer_id == user.id:
        return Role.FREELANCER
    return None


def require_party(contract: ContractRecord, user: User) -> Role:
    role = role_in_contract(contract, user)
    if role is None:
        raise HTTPException(status_code=404, detail="Not found")  # don't leak existence
    return role
