"""Persistence for verification runs, payment events and webhook dedupe."""

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.db import (
    CheckResultRecord,
    PaymentEvent,
    VerificationRunRecord,
    WebhookEventRecord,
    utcnow,
)
from app.verification.report import VerificationReport


async def start_verification_run(
    session: AsyncSession, milestone_id: str, submission_id: str
) -> VerificationRunRecord:
    run = VerificationRunRecord(milestone_id=milestone_id, submission_id=submission_id)
    session.add(run)
    await session.commit()
    return run


async def save_verification_report(
    session: AsyncSession, run: VerificationRunRecord, report: VerificationReport
) -> VerificationRunRecord:
    run.status = "completed"
    run.overall = report.overall.value
    run.report = report.model_dump(mode="json")
    run.finished_at = utcnow()
    run.duration_ms = report.duration_ms
    for c in report.criteria:
        run.results.append(
            CheckResultRecord(
                criterion_code=c.criterion_id,
                check_type=c.check_type,
                verdict=c.verdict.value,
                confidence=c.confidence,
                passed=c.check.passed if c.check else None,
                summary=c.check.summary if c.check else "",
                reasoning=c.reasoning,
                fix_suggestion=c.fix_suggestion,
                decided_by=c.decided_by,
                evidence=c.check.evidence.model_dump(mode="json") if c.check else {},
            )
        )
    await session.commit()
    return run


async def fail_verification_run(
    session: AsyncSession, run: VerificationRunRecord, error: str
) -> None:
    run.status = "error"
    run.error = error[:2000]
    run.finished_at = utcnow()
    await session.commit()


async def record_payment_event(
    session: AsyncSession,
    *,
    idempotency_key: str,
    kind: str,
    status: str,
    milestone_id: str | None = None,
    paypal_id: str | None = None,
    amount: Decimal | None = None,
    currency: str | None = None,
    raw: dict[str, Any] | None = None,
) -> tuple[PaymentEvent, bool]:
    """Insert once per idempotency key. Returns (event, created)."""
    existing = await session.scalar(
        select(PaymentEvent).where(PaymentEvent.idempotency_key == idempotency_key)
    )
    if existing:
        return existing, False
    event = PaymentEvent(
        idempotency_key=idempotency_key,
        kind=kind,
        status=status,
        milestone_id=milestone_id,
        paypal_id=paypal_id,
        amount=amount,
        currency=currency,
        raw=raw or {},
    )
    session.add(event)
    try:
        await session.commit()
    except IntegrityError:  # lost a race with a concurrent insert of the same key
        await session.rollback()
        existing = await session.scalar(
            select(PaymentEvent).where(PaymentEvent.idempotency_key == idempotency_key)
        )
        assert existing is not None
        return existing, False
    return event, True


class SqlEventStore:
    """Webhook dedupe backed by a primary key, so it survives restarts and multiple workers."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def mark_processed(self, event_id: str, event_type: str = "") -> bool:
        async with self._session_factory() as session:
            session.add(WebhookEventRecord(event_id=event_id, event_type=event_type))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False
            return True
