import base64
import binascii

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from app.agents.contract_agent import ContractAgent
from app.agents.contract_schema import Contract, ContractRequest
from app.core.llm import LLMError

router = APIRouter(prefix="/contracts", tags=["contracts"])

MAX_MOCKUP_BYTES = 5 * 1024 * 1024


class DraftContractRequest(ContractRequest):
    mockup_base64: str | None = Field(None, description="Optional PNG/JPEG/WebP mockup")


@router.post("/draft", response_model=Contract)
async def draft_contract(body: DraftContractRequest, request: Request) -> Contract:
    """Generate an editable contract draft. Nothing is saved until both parties approve."""
    llm = request.app.state.llm
    if llm is None:
        raise HTTPException(status_code=503, detail="LLM is not configured (set OPENAI_API_KEY)")

    mockup = _decode_mockup(body.mockup_base64)
    contract_request = ContractRequest.model_validate(body.model_dump(exclude={"mockup_base64"}))
    try:
        return await ContractAgent(llm).run(contract_request, mockup)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"Contract generation failed: {exc}") from exc


def _decode_mockup(data: str | None) -> bytes | None:
    if not data:
        return None
    try:
        mockup = base64.b64decode(data.split(",", 1)[-1], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="mockup_base64 is not valid base64") from exc
    if len(mockup) > MAX_MOCKUP_BYTES:
        raise HTTPException(status_code=413, detail="Mockup image is larger than 5 MB")
    return mockup
