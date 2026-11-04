import base64
import binascii
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_, select

from app.agents.contract_agent import ContractAgent
from app.agents.contract_schema import Contract, ContractRequest
from app.api.deps import SessionDep, SettingsDep, UserDep, require_party, role_in_contract
from app.api.schemas import ContractOut
from app.core.llm import LLMError
from app.models.db import ContractRecord, User, UserRole
from app.services.contracts import approve_contract, create_contract, get_contract

router = APIRouter(prefix="/contracts", tags=["contracts"])

MAX_MOCKUP_BYTES = 5 * 1024 * 1024


class DraftContractRequest(ContractRequest):
    mockup_base64: str | None = Field(None, description="Optional PNG/JPEG/WebP mockup")


class CreateContractRequest(BaseModel):
    contract: Contract = Field(description="The (possibly edited) draft both parties will approve")
    freelancer_email: EmailStr | None = None
    mockup_base64: str | None = None


@router.post("/draft", response_model=Contract)
async def draft_contract(body: DraftContractRequest, request: Request, _: UserDep) -> Contract:
    """Generate an editable contract draft. Nothing is saved until the client creates it."""
    llm = request.app.state.llm
    if llm is None:
        raise HTTPException(status_code=503, detail="LLM is not configured (set OPENAI_API_KEY)")
    mockup = _decode_mockup(body.mockup_base64)
    contract_request = ContractRequest.model_validate(body.model_dump(exclude={"mockup_base64"}))
    try:
        return await ContractAgent(llm).run(contract_request, mockup)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"Contract generation failed: {exc}") from exc


@router.post("", response_model=ContractOut, status_code=201)
async def create(
    body: CreateContractRequest, user: UserDep, session: SessionDep, settings: SettingsDep
) -> ContractOut:
    if user.role is not UserRole.CLIENT:
        raise HTTPException(status_code=403, detail="Only clients create contracts")
    if body.contract.total != sum(m.amount for m in body.contract.milestones):
        raise HTTPException(status_code=422, detail="Milestone amounts must add up to the total")

    freelancer = None
    if body.freelancer_email:
        freelancer = await session.scalar(select(User).where(User.email == body.freelancer_email))
        if freelancer is None or freelancer.role is not UserRole.FREELANCER:
            raise HTTPException(status_code=422, detail="No freelancer with that email")

    mockup_path = None
    if mockup := _decode_mockup(body.mockup_base64):
        mockups = settings.artifacts_dir / "mockups"
        mockups.mkdir(parents=True, exist_ok=True)
        path = mockups / f"{uuid.uuid4().hex}.img"
        path.write_bytes(mockup)
        mockup_path = str(path)

    contract = await create_contract(
        session, body.contract, client=user, freelancer=freelancer, mockup_path=mockup_path
    )
    return ContractOut.of(contract, role=role_in_contract(contract, user), settings=settings)


@router.get("", response_model=list[ContractOut])
async def list_contracts(
    user: UserDep, session: SessionDep, settings: SettingsDep
) -> list[ContractOut]:
    query = select(ContractRecord.id).order_by(ContractRecord.created_at.desc())
    if user.role is not UserRole.ADMIN:
        query = query.where(
            or_(ContractRecord.client_id == user.id, ContractRecord.freelancer_id == user.id)
        )
    contracts = [await get_contract(session, cid) for cid in await session.scalars(query)]
    return [ContractOut.of(c, role=role_in_contract(c, user), settings=settings) for c in contracts]


@router.get("/{contract_id}", response_model=ContractOut)
async def read(
    contract_id: str, user: UserDep, session: SessionDep, settings: SettingsDep
) -> ContractOut:
    contract = await get_contract(session, contract_id)
    return ContractOut.of(contract, role=require_party(contract, user), settings=settings)


@router.post("/{contract_id}/approve", response_model=ContractOut)
async def approve(
    contract_id: str, user: UserDep, session: SessionDep, settings: SettingsDep
) -> ContractOut:
    """Each party approves the criteria. Once both have, milestones become AGREED."""
    contract = await approve_contract(session, contract_id, user)
    return ContractOut.of(contract, role=role_in_contract(contract, user), settings=settings)


def _decode_mockup(data: str | None) -> bytes | None:
    if not data:
        return None
    try:
        mockup = base64.b64decode(data.split(",", 1)[-1], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="mockup_base64 is not valid base64") from exc
    if len(mockup) > MAX_MOCKUP_BYTES:
        raise HTTPException(status_code=413, detail="Mockup image is larger than 5 MB")
    if not mockup.startswith((b"\x89PNG", b"\xff\xd8")) and mockup[8:12] != b"WEBP":
        raise HTTPException(status_code=422, detail="Mockup must be a PNG, JPEG or WebP image")
    return mockup
