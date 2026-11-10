from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import SessionDep, SettingsDep
from app.services.demo import reset_demo

router = APIRouter(prefix="/demo", tags=["demo"])


class DemoState(BaseModel):
    contract_id: str
    milestone_id: str
    demo_sites_url: str


@router.post("/reset", response_model=DemoState)
async def reset(session: SessionDep, settings: SettingsDep) -> DemoState:
    """Judge demo mode: wipe everything and seed a ready-to-fund bakery contract."""
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Demo mode is off")
    seeded = await reset_demo(session, settings)
    return DemoState(**seeded, demo_sites_url=f"{settings.backend_url}/demo-sites")
