"""The only code in PactPay that moves money.

Rules:
- Every PayPal call that moves money uses an idempotency key derived from the milestone
  (e.g. "capture-<milestone id>"), so a retry after a crash or timeout never double-captures
  or double-pays: PayPal returns the original result instead.
- `release()` is resumable: it records each step (capture id, payout batch id) as soon as
  PayPal confirms it and skips steps already done, so it can be re-run safely at any point.
- State changes go through MilestoneService, so the state machine guards every move.
- Webhooks reconcile anything we missed (e.g. we crashed after PayPal captured but before
  we saved the capture id).
- AI agents never call this module directly; only API actions by authorized users and the
  auto-approve timer do.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.db import MilestoneRecord, User, UserRole, utcnow
from app.paypal.errors import PayPalError
from app.paypal.orders import Authorization, AuthorizedOrder, Capture, CreatedOrder
from app.paypal.payouts import PayoutBatch, split_amount
from app.paypal.webhooks import EventHandler, EventType, WebhookEvent
from app.services.milestones import MilestoneService
from app.services.records import record_payment_event
from app.workflows.milestone_state import Action, MilestoneState, Role

logger = logging.getLogger(__name__)

PAYOUT_SUCCESS = "SUCCESS"
PAYOUT_FAILED = {"FAILED", "RETURNED", "BLOCKED", "DENIED", "REFUNDED", "REVERSED"}
EXPIRED_ISSUES = ("AUTHORIZATION_EXPIRED", "AUTHORIZATION_VOIDED")


class PaymentError(Exception):
    """A money operation can't proceed; the message is safe to show to users."""


class OrdersPort(Protocol):
    async def create_authorize_order(self, **kwargs: object) -> CreatedOrder: ...
    async def authorize_order(
        self, order_id: str, *, request_id: str | None = None
    ) -> AuthorizedOrder: ...
    async def create_sandbox_card_authorization(self, **kwargs: object) -> AuthorizedOrder: ...
    async def capture_authorization(self, authorization_id: str, *, request_id: str) -> Capture: ...
    async def void_authorization(self, authorization_id: str, *, request_id: str) -> None: ...
    async def reauthorize_authorization(
        self, authorization_id: str, *, request_id: str
    ) -> Authorization: ...


class PayoutsPort(Protocol):
    async def pay_freelancer(
        self, *, receiver_email: str, amount: Decimal | str, currency: str, sender_batch_id: str
    ) -> PayoutBatch: ...
    async def get_payout_batch(self, payout_batch_id: str) -> PayoutBatch: ...


class PaymentRuntime:
    """Long-lived dependencies shared by all requests (held on app.state)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        orders: OrdersPort,
        payouts: PayoutsPort,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.orders = orders
        self.payouts = payouts
        self.settings = settings
        # Serializes money operations per milestone within this process. Across processes,
        # the state machine + optimistic locking reject the loser.
        self.locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def service(self, session: AsyncSession) -> "PaymentService":
        return PaymentService(session, self)

    async def poll_payout(self, milestone_id: str) -> None:
        """Poll until the payout settles (webhooks also do this when reachable)."""
        for _ in range(self.settings.payout_poll_attempts):
            await asyncio.sleep(self.settings.payout_poll_seconds)
            async with self.session_factory() as session:
                milestone = await self.service(session).refresh_payout(milestone_id)
                if milestone.state is MilestoneState.RELEASED or (
                    milestone.payout_status in PAYOUT_FAILED
                ):
                    return

    async def auto_approve_due(self) -> list[str]:
        """Release PASSED milestones whose client stayed silent past the timeout."""
        cutoff = utcnow() - timedelta(hours=self.settings.auto_approve_hours)
        async with self.session_factory() as session:
            due = list(
                await session.scalars(
                    select(MilestoneRecord.id).where(
                        MilestoneRecord.state == MilestoneState.PASSED,
                        MilestoneRecord.passed_at < cutoff,
                    )
                )
            )
        released = []
        for milestone_id in due:
            async with self.session_factory() as session:
                try:
                    await self.service(session).approve_release(milestone_id, user=None)
                    released.append(milestone_id)
                except (PaymentError, PayPalError):
                    logger.exception("Auto-approve failed for %s", milestone_id)
        return released

    def webhook_handlers(self) -> dict[str, EventHandler]:
        async def with_service(event: WebhookEvent) -> None:
            async with self.session_factory() as session:
                await self.service(session).reconcile(event)

        return {event_type.value: with_service for event_type in EventType}


class PaymentService:
    def __init__(self, session: AsyncSession, runtime: PaymentRuntime) -> None:
        self._session = session
        self._rt = runtime
        self._milestones = MilestoneService(session)

    @property
    def _currency(self) -> str:
        return self._rt.settings.paypal_currency

    # -------------------------------------------------------------- funding

    async def start_funding(self, milestone_id: str, user: User) -> CreatedOrder:
        milestone = await self._milestones.get(milestone_id)
        self._require_client(milestone, user)
        if milestone.state is not MilestoneState.AGREED:
            raise PaymentError(
                f"Milestone is {milestone.state}; only agreed milestones can be funded"
            )
        settings = self._rt.settings
        order = await self._rt.orders.create_authorize_order(
            amount=milestone.amount,
            currency=self._currency,
            reference_id=milestone.id,
            description=f"PactPay escrow: {milestone.title}",
            return_url=f"{settings.backend_url}/paypal/return",
            cancel_url=f"{settings.frontend_url}/milestones/{milestone.id}?funding=cancelled",
            request_id=f"order-{milestone.id}",
        )
        await self._milestones.record(
            milestone, actor=user.id, event="order_created", updates={"paypal_order_id": order.id}
        )
        await record_payment_event(
            self._session,
            idempotency_key=f"order-{order.id}",
            kind="order_created",
            status=order.status,
            milestone_id=milestone.id,
            paypal_id=order.id,
            amount=milestone.amount,
            currency=self._currency,
        )
        return order

    async def complete_funding(self, order_id: str) -> MilestoneRecord:
        """Buyer approved on PayPal; place the hold. Idempotent (return URL may be hit twice)."""
        milestone = await self._milestones.find_by(paypal_order_id=order_id)
        if milestone is None:
            raise PaymentError("Unknown PayPal order")
        async with self._rt.locks[milestone.id]:
            milestone = await self._milestones.get(milestone.id)
            if milestone.paypal_authorization_id:
                return milestone
            authorized = await self._rt.orders.authorize_order(
                order_id, request_id=f"authorize-{order_id}"
            )
            return await self._mark_funded(milestone, authorized, actor="paypal")

    async def fund_with_sandbox_card(self, milestone_id: str, user: User) -> MilestoneRecord:
        """Judge demo mode: fund instantly with PayPal's sandbox test card (no checkout popup)."""
        if self._rt.settings.paypal_mode != "sandbox":
            raise PaymentError("Test-card funding is only available in sandbox mode")
        milestone = await self._milestones.get(milestone_id)
        self._require_client(milestone, user)
        if milestone.state is not MilestoneState.AGREED:
            raise PaymentError(
                f"Milestone is {milestone.state}; only agreed milestones can be funded"
            )
        async with self._rt.locks[milestone.id]:
            authorized = await self._rt.orders.create_sandbox_card_authorization(
                amount=milestone.amount,
                currency=self._currency,
                reference_id=milestone.id,
                description=f"PactPay escrow: {milestone.title}",
                request_id=f"card-order-{milestone.id}",
            )
            await self._milestones.record(
                milestone,
                actor=user.id,
                event="order_created",
                updates={"paypal_order_id": authorized.order_id},
            )
            return await self._mark_funded(milestone, authorized, actor=user.id)

    async def _mark_funded(
        self, milestone: MilestoneRecord, authorized: AuthorizedOrder, *, actor: str
    ) -> MilestoneRecord:
        auth = authorized.authorization
        if auth.status != "CREATED":
            raise PaymentError(f"PayPal authorization is {auth.status}, not CREATED")
        await record_payment_event(
            self._session,
            idempotency_key=f"authorize-{auth.id}",
            kind="authorized",
            status=auth.status,
            milestone_id=milestone.id,
            paypal_id=auth.id,
            amount=milestone.amount,
            currency=self._currency,
        )
        return await self._milestones.apply(
            milestone,
            Action.FUND,
            role=Role.SYSTEM,
            actor=actor,
            updates={
                "paypal_order_id": authorized.order_id,
                "paypal_authorization_id": auth.id,
                "authorization_expires_at": auth.expiration_time,
            },
        )

    # --------------------------------------------------------------- cancel

    async def cancel(self, milestone_id: str, user: User) -> MilestoneRecord:
        milestone = await self._milestones.get(milestone_id)
        role = self._require_client_or_admin(milestone, user)
        async with self._rt.locks[milestone.id]:
            milestone = await self._milestones.get(milestone_id)
            if milestone.state in {MilestoneState.DRAFT, MilestoneState.AGREED}:
                return await self._milestones.apply(
                    milestone, Action.CANCEL, role=role, actor=user.id
                )
            if milestone.paypal_capture_id:
                raise PaymentError("Money was already captured; this needs a dispute, not a cancel")
            if milestone.paypal_authorization_id and not milestone.authorization_voided:
                await self._rt.orders.void_authorization(
                    milestone.paypal_authorization_id, request_id=f"void-{milestone.id}"
                )
                await record_payment_event(
                    self._session,
                    idempotency_key=f"void-{milestone.id}",
                    kind="voided",
                    status="VOIDED",
                    milestone_id=milestone.id,
                    paypal_id=milestone.paypal_authorization_id,
                )
            return await self._milestones.apply(
                milestone,
                Action.REFUND,
                role=role,
                actor=user.id,
                updates={"authorization_voided": True},
            )

    # -------------------------------------------------------------- release

    async def approve_release(self, milestone_id: str, *, user: User | None) -> MilestoneRecord:
        """Client approval (or the auto-approve timer when user is None), then release."""
        milestone = await self._milestones.get(milestone_id)
        if user is not None:
            self._require_client(milestone, user)
        await self._freelancer_payout_email(milestone)  # fail BEFORE taking any money
        async with self._rt.locks[milestone.id]:
            milestone = await self._milestones.get(milestone_id)
            if milestone.state is MilestoneState.PASSED:
                await self._milestones.apply(
                    milestone,
                    Action.APPROVE_RELEASE,
                    role=Role.CLIENT if user else Role.SYSTEM,
                    actor=user.id if user else "auto-approve",
                )
        return await self.release(milestone_id)

    async def release(self, milestone_id: str) -> MilestoneRecord:
        """Capture, then pay out. Safe to call repeatedly; each step runs at most once."""
        async with self._rt.locks[milestone_id]:
            milestone = await self._milestones.get(milestone_id)
            if milestone.state is MilestoneState.RELEASED:
                return milestone
            if milestone.state is not MilestoneState.RELEASING:
                raise PaymentError(f"Milestone is {milestone.state}; nothing to release")
            receiver = await self._freelancer_payout_email(milestone)

            if not milestone.paypal_capture_id:
                capture = await self._capture(milestone)
                await self._milestones.record(
                    milestone,
                    actor="executor",
                    event="captured",
                    updates={"paypal_capture_id": capture.id},
                )

            if not milestone.payout_batch_id:
                split = split_amount(milestone.amount, self._rt.settings.platform_fee_percent)
                batch = await self._rt.payouts.pay_freelancer(
                    receiver_email=receiver,
                    amount=split.net,
                    currency=self._currency,
                    sender_batch_id=f"payout-{milestone.id}",
                )
                await record_payment_event(
                    self._session,
                    idempotency_key=f"payout-{milestone.id}",
                    kind="payout_sent",
                    status=batch.batch_status,
                    milestone_id=milestone.id,
                    paypal_id=batch.payout_batch_id,
                    amount=split.net,
                    currency=self._currency,
                    raw={"gross": str(split.gross), "fee": str(split.fee)},
                )
                await self._milestones.record(
                    milestone,
                    actor="executor",
                    event="payout_sent",
                    updates={
                        "payout_batch_id": batch.payout_batch_id,
                        "payout_status": batch.batch_status,
                    },
                )
        return await self.refresh_payout(milestone_id)

    async def refresh_payout(self, milestone_id: str) -> MilestoneRecord:
        milestone = await self._milestones.get(milestone_id)
        if milestone.state is not MilestoneState.RELEASING or not milestone.payout_batch_id:
            return milestone
        batch = await self._rt.payouts.get_payout_batch(milestone.payout_batch_id)
        item_status = batch.items[0].transaction_status if batch.items else None
        return await self._apply_payout_status(milestone, item_status or batch.batch_status)

    async def _apply_payout_status(
        self, milestone: MilestoneRecord, status: str
    ) -> MilestoneRecord:
        if status == PAYOUT_SUCCESS and milestone.state is MilestoneState.RELEASING:
            return await self._milestones.apply(
                milestone,
                Action.RELEASE_COMPLETED,
                role=Role.SYSTEM,
                actor="paypal",
                updates={"payout_status": PAYOUT_SUCCESS},
            )
        if status != milestone.payout_status and milestone.state is MilestoneState.RELEASING:
            await self._milestones.record(
                milestone, actor="paypal", event="payout_status", updates={"payout_status": status}
            )
        return milestone

    async def _capture(self, milestone: MilestoneRecord) -> Capture:
        assert milestone.paypal_authorization_id
        try:
            capture = await self._rt.orders.capture_authorization(
                milestone.paypal_authorization_id, request_id=f"capture-{milestone.id}"
            )
        except PayPalError as exc:
            if not any(exc.has_issue(issue) for issue in EXPIRED_ISSUES):
                raise PaymentError(f"PayPal capture failed: {exc.message}") from exc
            capture = await self._reauthorize_and_capture(milestone)
        await record_payment_event(
            self._session,
            idempotency_key=f"capture-{milestone.id}",
            kind="captured",
            status=capture.status,
            milestone_id=milestone.id,
            paypal_id=capture.id,
            amount=milestone.amount,
            currency=self._currency,
        )
        if capture.status not in {"COMPLETED", "PENDING"}:
            raise PaymentError(f"PayPal capture status is {capture.status}")
        return capture

    async def _reauthorize_and_capture(self, milestone: MilestoneRecord) -> Capture:
        """The hold expired (long milestone). Renew it once, or ask the client to re-fund."""
        assert milestone.paypal_authorization_id
        try:
            renewed = await self._rt.orders.reauthorize_authorization(
                milestone.paypal_authorization_id, request_id=f"reauthorize-{milestone.id}"
            )
        except PayPalError as exc:
            raise PaymentError(
                "The PayPal authorization expired and could not be renewed; "
                "the client needs to fund this milestone again"
            ) from exc
        await self._milestones.record(
            milestone,
            actor="executor",
            event="reauthorized",
            updates={
                "paypal_authorization_id": renewed.id,
                "authorization_expires_at": renewed.expiration_time,
            },
        )
        return await self._rt.orders.capture_authorization(
            renewed.id, request_id=f"capture-{milestone.id}-{renewed.id}"
        )

    # ----------------------------------------------------------- webhooks

    async def reconcile(self, event: WebhookEvent) -> None:
        """Bring our records in line with PayPal's (the source of truth for money)."""
        resource = event.resource
        related = resource.get("supplementary_data", {}).get("related_ids", {})
        match event.event_type:
            case EventType.AUTHORIZATION_CREATED:
                milestone = await self._milestones.find_by(
                    paypal_order_id=related.get("order_id", "")
                )
                if milestone and milestone.state is MilestoneState.AGREED:
                    # Buyer approved but never came back to our return URL.
                    await self._milestones.apply(
                        milestone,
                        Action.FUND,
                        role=Role.SYSTEM,
                        actor="paypal-webhook",
                        updates={"paypal_authorization_id": resource["id"]},
                    )
            case EventType.AUTHORIZATION_VOIDED:
                milestone = await self._milestones.find_by(paypal_authorization_id=resource["id"])
                if milestone and not milestone.authorization_voided:
                    await self._milestones.record(
                        milestone,
                        actor="paypal-webhook",
                        event="authorization_voided",
                        updates={"authorization_voided": True},
                    )
            case EventType.CAPTURE_COMPLETED:
                milestone = await self._milestones.find_by(
                    paypal_authorization_id=related.get("authorization_id", "")
                )
                if milestone and not milestone.paypal_capture_id:
                    # We crashed after PayPal captured but before we saved it.
                    await self._milestones.record(
                        milestone,
                        actor="paypal-webhook",
                        event="captured",
                        updates={"paypal_capture_id": resource["id"]},
                    )
            case EventType.PAYOUTS_ITEM_SUCCEEDED | EventType.PAYOUTS_ITEM_FAILED:
                milestone = await self._milestones.find_by(
                    payout_batch_id=resource.get("payout_batch_id", "")
                )
                if milestone:
                    status = resource.get("transaction_status") or (
                        PAYOUT_SUCCESS
                        if event.event_type == EventType.PAYOUTS_ITEM_SUCCEEDED
                        else "FAILED"
                    )
                    await self._apply_payout_status(milestone, status)
            case _:
                return

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _require_client(milestone: MilestoneRecord, user: User) -> None:
        if milestone.contract.client_id != user.id:
            raise PermissionError("Only this contract's client can do that")

    @staticmethod
    def _require_client_or_admin(milestone: MilestoneRecord, user: User) -> Role:
        if user.role is UserRole.ADMIN:
            return Role.ADMIN
        if milestone.contract.client_id == user.id:
            return Role.CLIENT
        raise PermissionError("Only this contract's client or an admin can do that")

    async def _freelancer_payout_email(self, milestone: MilestoneRecord) -> str:
        freelancer = (
            await self._session.get(User, milestone.contract.freelancer_id)
            if milestone.contract.freelancer_id
            else None
        )
        if freelancer is None or not freelancer.paypal_email:
            raise PaymentError("The freelancer has no PayPal email for payouts")
        return freelancer.paypal_email
