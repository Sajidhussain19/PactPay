import asyncio
import json
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.api.deps import (
    PaymentsDep,
    SessionDep,
    SettingsDep,
    UserDep,
    VerifierDep,
    require_party,
)
from app.api.schemas import MilestoneOut, RunOut
from app.models.db import MilestoneRecord, SubmissionRecord, User
from app.services.milestones import MilestoneService
from app.services.verification_manager import latest_run
from app.workflows.milestone_state import Action, MilestoneState, Role

router = APIRouter(tags=["milestones"])

SSE_KEEPALIVE_SECONDS = 15


class FundRequest(BaseModel):
    method: Literal["paypal", "sandbox_card"] = "paypal"


class FundResponse(BaseModel):
    approve_url: str | None = None
    milestone: MilestoneOut


class AuthorizeRequest(BaseModel):
    order_id: str


class SubmissionRequest(BaseModel):
    live_url: str = Field(max_length=2000)
    repo_url: str | None = Field(None, max_length=2000)
    notes: str | None = Field(None, max_length=2000)

    @field_validator("live_url")
    @classmethod
    def _http(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("live_url must be an http(s) URL")
        return value


class RunStarted(BaseModel):
    run_id: str
    milestone: MilestoneOut


class ReviewRequest(BaseModel):
    decision: Literal["pass", "fail"]
    note: str | None = Field(None, max_length=2000)


class DisputeRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=2000)


async def _load(session: SessionDep, milestone_id: str, user: User) -> tuple[MilestoneRecord, Role]:
    milestone = await MilestoneService(session).get(milestone_id)
    return milestone, require_party(milestone.contract, user)


async def _out(
    session: SessionDep, milestone_id: str, user: User, settings: SettingsDep
) -> MilestoneOut:
    milestone, role = await _load(session, milestone_id, user)
    run = await latest_run(session, milestone)
    return MilestoneOut.of(milestone, role=role, settings=settings, latest_run=run)


@router.get("/milestones/{milestone_id}", response_model=MilestoneOut)
async def read(
    milestone_id: str, user: UserDep, session: SessionDep, settings: SettingsDep
) -> MilestoneOut:
    return await _out(session, milestone_id, user, settings)


# ------------------------------------------------------------------ funding


@router.post("/milestones/{milestone_id}/fund", response_model=FundResponse)
async def fund(
    milestone_id: str,
    body: FundRequest,
    user: UserDep,
    session: SessionDep,
    settings: SettingsDep,
    payments: PaymentsDep,
) -> FundResponse:
    """Client funds a milestone. PayPal *authorizes* (holds) the money; nothing is captured."""
    await _load(session, milestone_id, user)
    if body.method == "sandbox_card":
        await payments.fund_with_sandbox_card(milestone_id, user)
        return FundResponse(milestone=await _out(session, milestone_id, user, settings))
    order = await payments.start_funding(milestone_id, user)
    return FundResponse(
        approve_url=order.approve_url, milestone=await _out(session, milestone_id, user, settings)
    )


@router.post("/milestones/{milestone_id}/authorize", response_model=MilestoneOut)
async def authorize(
    milestone_id: str,
    body: AuthorizeRequest,
    user: UserDep,
    session: SessionDep,
    settings: SettingsDep,
    payments: PaymentsDep,
) -> MilestoneOut:
    """Called by the PayPal JS SDK's onApprove: place the hold for an approved order."""
    milestone, _ = await _load(session, milestone_id, user)
    if milestone.paypal_order_id != body.order_id:
        raise HTTPException(status_code=422, detail="Order does not belong to this milestone")
    await payments.complete_funding(body.order_id)
    return await _out(session, milestone_id, user, settings)


@router.get("/paypal/return", include_in_schema=False)
async def paypal_return(
    token: str, payments: PaymentsDep, settings: SettingsDep
) -> RedirectResponse:
    """Buyer is redirected here after approving on PayPal (token = order id)."""
    milestone = await payments.complete_funding(token)
    return RedirectResponse(f"{settings.frontend_url}/milestones/{milestone.id}?funded=1")


# --------------------------------------------------------------- delivery


@router.post("/milestones/{milestone_id}/submissions", response_model=RunStarted, status_code=201)
async def submit(
    milestone_id: str,
    body: SubmissionRequest,
    user: UserDep,
    session: SessionDep,
    settings: SettingsDep,
    verifier: VerifierDep,
) -> RunStarted:
    """Freelancer delivers; verification starts immediately in the background."""
    milestone, role = await _load(session, milestone_id, user)
    if role is not Role.FREELANCER:
        raise HTTPException(status_code=403, detail="Only the freelancer can submit work")
    if milestone.state not in {MilestoneState.FUNDED, MilestoneState.FAILED}:
        raise HTTPException(status_code=409, detail=f"Milestone is {milestone.state}; can't submit")
    session.add(
        SubmissionRecord(
            milestone_id=milestone.id,
            attempt=len(milestone.submissions) + 1,
            live_url=body.live_url,
            repo_url=body.repo_url,
            notes=body.notes,
        )
    )
    await session.commit()
    milestone = await MilestoneService(session).get(milestone_id)
    await MilestoneService(session).apply(milestone, Action.SUBMIT, role=role, actor=user.id)
    run_id = await verifier.start(milestone_id, actor=user.id)
    return RunStarted(run_id=run_id, milestone=await _out(session, milestone_id, user, settings))


@router.post("/milestones/{milestone_id}/verify", response_model=RunStarted)
async def verify(
    milestone_id: str,
    user: UserDep,
    session: SessionDep,
    settings: SettingsDep,
    verifier: VerifierDep,
) -> RunStarted:
    """Re-run verification on the latest submission (e.g. after a verifier error)."""
    await _load(session, milestone_id, user)
    run_id = await verifier.start(milestone_id, actor=user.id)
    return RunStarted(run_id=run_id, milestone=await _out(session, milestone_id, user, settings))


@router.get("/milestones/{milestone_id}/report", response_model=RunOut)
async def report(milestone_id: str, user: UserDep, session: SessionDep) -> RunOut:
    milestone, _ = await _load(session, milestone_id, user)
    run = await latest_run(session, milestone)
    if run is None:
        raise HTTPException(status_code=404, detail="No verification has run yet")
    return RunOut.of(run)


@router.get("/milestones/{milestone_id}/verification/stream")
async def stream(
    milestone_id: str, request: Request, user: UserDep, session: SessionDep, verifier: VerifierDep
) -> StreamingResponse:
    """Server-Sent Events: each check as it starts, finishes and is judged."""
    milestone, _ = await _load(session, milestone_id, user)
    run = await latest_run(session, milestone)
    channel = verifier.channel(run.id) if run else None

    async def events() -> AsyncIterator[str]:
        if channel is None:  # nothing in flight in this process: send the final state
            payload = {"type": "report_ready", "status": run.overall if run else None}
            yield _sse(payload)
            return
        iterator = channel.stream().__aiter__()
        while True:
            try:
                event = await asyncio.wait_for(iterator.__anext__(), SSE_KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keep-alive\n\n"  # stops proxies from closing an idle stream
                continue
            except StopAsyncIteration:
                return
            if await request.is_disconnected():
                return
            yield _sse(event.model_dump(mode="json", exclude_none=True))

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


# ---------------------------------------------------------------- decisions


@router.post("/milestones/{milestone_id}/review", response_model=MilestoneOut)
async def review(
    milestone_id: str,
    body: ReviewRequest,
    user: UserDep,
    session: SessionDep,
    settings: SettingsDep,
) -> MilestoneOut:
    """Human decision on a NEEDS_REVIEW verdict (client or admin)."""
    milestone, role = await _load(session, milestone_id, user)
    action = Action.REVIEW_PASS if body.decision == "pass" else Action.REVIEW_FAIL
    await MilestoneService(session).apply(
        milestone, action, role=role, actor=user.id, details={"note": body.note}
    )
    return await _out(session, milestone_id, user, settings)


@router.post("/milestones/{milestone_id}/approve-release", response_model=MilestoneOut)
async def approve_release(
    milestone_id: str,
    request: Request,
    user: UserDep,
    session: SessionDep,
    settings: SettingsDep,
    payments: PaymentsDep,
) -> MilestoneOut:
    """Client approves: capture the held payment, then pay the freelancer."""
    await _load(session, milestone_id, user)
    milestone = await payments.approve_release(milestone_id, user=user)
    _follow_payout(request, milestone)
    return await _out(session, milestone_id, user, settings)


@router.post("/milestones/{milestone_id}/retry-release", response_model=MilestoneOut)
async def retry_release(
    milestone_id: str,
    request: Request,
    user: UserDep,
    session: SessionDep,
    settings: SettingsDep,
    payments: PaymentsDep,
) -> MilestoneOut:
    """Resume a release that stopped part-way (e.g. payout failed). Idempotent."""
    _, role = await _load(session, milestone_id, user)
    if role not in {Role.CLIENT, Role.ADMIN}:
        raise HTTPException(status_code=403, detail="Only the client or an admin can retry")
    milestone = await payments.release(milestone_id)
    _follow_payout(request, milestone)
    return await _out(session, milestone_id, user, settings)


@router.post("/milestones/{milestone_id}/dispute", response_model=MilestoneOut)
async def dispute(
    milestone_id: str,
    body: DisputeRequest,
    user: UserDep,
    session: SessionDep,
    settings: SettingsDep,
) -> MilestoneOut:
    """Client requests changes on a passed report, or freelancer disputes a failed one."""
    milestone, role = await _load(session, milestone_id, user)
    await MilestoneService(session).apply(
        milestone, Action.DISPUTE, role=role, actor=user.id, details={"reason": body.reason}
    )
    return await _out(session, milestone_id, user, settings)


@router.post("/milestones/{milestone_id}/cancel", response_model=MilestoneOut)
async def cancel(
    milestone_id: str,
    user: UserDep,
    session: SessionDep,
    settings: SettingsDep,
    payments: PaymentsDep,
) -> MilestoneOut:
    """Cancel. If funds are held, the PayPal authorization is voided (client refunded)."""
    await _load(session, milestone_id, user)
    await payments.cancel(milestone_id, user)
    return await _out(session, milestone_id, user, settings)


def _follow_payout(request: Request, milestone: MilestoneRecord) -> None:
    """Payouts settle asynchronously; poll in the background in case webhooks can't reach us."""
    if milestone.state is MilestoneState.RELEASING and milestone.payout_batch_id:
        task = asyncio.create_task(request.app.state.payments.poll_payout(milestone.id))
        request.app.state.background_tasks.add(task)
        task.add_done_callback(request.app.state.background_tasks.discard)
