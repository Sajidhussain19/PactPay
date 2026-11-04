from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import SessionDep, SettingsDep, UserDep
from app.api.schemas import UserOut
from app.core.security import issue_token
from app.models.db import User, UserRole
from app.services.demo import DEMO_USERS

router = APIRouter(prefix="/auth", tags=["auth"])


class DemoLoginRequest(BaseModel):
    role: UserRole


class LoginResponse(BaseModel):
    token: str
    user: UserOut


@router.post("/demo-login", response_model=LoginResponse)
async def demo_login(
    body: DemoLoginRequest, session: SessionDep, settings: SettingsDep
) -> LoginResponse:
    """One-click login as the seeded demo client, freelancer or admin (judge demo mode)."""
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Demo mode is off")
    email, _ = DEMO_USERS[body.role]
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        raise HTTPException(status_code=503, detail="Demo users are not seeded")
    token = issue_token(user.id, settings.secret_key.get_secret_value())
    return LoginResponse(token=token, user=UserOut.of(user))


@router.get("/me", response_model=UserOut)
async def me(user: UserDep) -> UserOut:
    return UserOut.of(user)
