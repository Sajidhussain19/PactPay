"""The only way milestone state changes. Every change is checked against the state
machine, written to the audit log, and protected against concurrent updates."""

from dataclasses import replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.exc import StaleDataError

from app.models.db import AuditEvent, MilestoneRecord, utcnow
from app.workflows.milestone_state import (
    Action,
    MilestoneFacts,
    MilestoneState,
    Role,
    next_state,
)


class NotFoundError(Exception):
    pass


class ConcurrentUpdateError(Exception):
    """Someone else changed this milestone first. Reload and retry."""


# Fields callers may set as part of a transition (e.g. the authorization id on FUND).
UPDATABLE_FIELDS = {
    "paypal_order_id",
    "paypal_authorization_id",
    "authorization_expires_at",
    "authorization_voided",
    "paypal_capture_id",
    "payout_batch_id",
    "payout_status",
}

_TIMESTAMPS = {
    MilestoneState.PASSED: "passed_at",
    MilestoneState.RELEASED: "released_at",
}


def milestone_facts(milestone: MilestoneRecord) -> MilestoneFacts:
    return MilestoneFacts(
        state=milestone.state,
        contract_agreed=milestone.contract.is_agreed,
        authorization_id=milestone.paypal_authorization_id,
        has_submission=bool(milestone.submissions),
        capture_id=milestone.paypal_capture_id,
        payout_succeeded=milestone.payout_succeeded,
        authorization_voided=milestone.authorization_voided,
    )


class MilestoneService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, milestone_id: str) -> MilestoneRecord:
        milestone = await self._session.scalar(
            select(MilestoneRecord)
            .where(MilestoneRecord.id == milestone_id)
            .options(
                selectinload(MilestoneRecord.contract),
                selectinload(MilestoneRecord.criteria),
                selectinload(MilestoneRecord.submissions),
            )
            # Always read fresh rows: state decisions must not use a stale identity map.
            .execution_options(populate_existing=True)
        )
        if milestone is None:
            raise NotFoundError(f"Milestone {milestone_id} not found")
        return milestone

    async def apply(
        self,
        milestone: MilestoneRecord,
        action: Action,
        *,
        role: Role,
        actor: str,
        updates: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> MilestoneRecord:
        """Apply `updates`, then `action`, atomically. On any failure nothing is saved."""
        updates = updates or {}
        if unknown := set(updates) - UPDATABLE_FIELDS:
            raise ValueError(f"Fields not updatable via transitions: {sorted(unknown)}")

        milestone_id = milestone.id
        from_state = milestone.state
        # Decide first, mutate second: an illegal action leaves the record untouched.
        facts = replace(milestone_facts(milestone), **_facts_from_updates(updates))
        target = next_state(facts, action, role)

        for field, value in updates.items():
            setattr(milestone, field, value)
        milestone.state = target
        if field := _TIMESTAMPS.get(target):
            setattr(milestone, field, utcnow())
        if action is Action.APPROVE_RELEASE:
            milestone.client_approved_at = utcnow()
        self._session.add(
            AuditEvent(
                contract_id=milestone.contract_id,
                milestone_id=milestone_id,
                actor=actor,
                action=action.value,
                from_state=from_state.value,
                to_state=target.value,
                details={"role": role.value, **(details or {}), **_jsonable(updates)},
            )
        )
        try:
            await self._session.commit()
        except StaleDataError as exc:
            await self._session.rollback()  # expires the record: callers must reload it
            raise ConcurrentUpdateError(
                f"Milestone {milestone_id} was updated concurrently"
            ) from exc
        return milestone

    async def record(
        self,
        milestone: MilestoneRecord,
        *,
        actor: str,
        event: str,
        updates: dict[str, Any],
        details: dict[str, Any] | None = None,
    ) -> MilestoneRecord:
        """Save PayPal references (e.g. a capture id) without changing state. Audited."""
        if unknown := set(updates) - UPDATABLE_FIELDS:
            raise ValueError(f"Fields not updatable: {sorted(unknown)}")
        milestone_id = milestone.id
        for field, value in updates.items():
            setattr(milestone, field, value)
        self._session.add(
            AuditEvent(
                contract_id=milestone.contract_id,
                milestone_id=milestone_id,
                actor=actor,
                action=event,
                from_state=milestone.state.value,
                to_state=milestone.state.value,
                details={**(details or {}), **_jsonable(updates)},
            )
        )
        try:
            await self._session.commit()
        except StaleDataError as exc:
            await self._session.rollback()
            raise ConcurrentUpdateError(
                f"Milestone {milestone_id} was updated concurrently"
            ) from exc
        return milestone

    async def find_by(self, **refs: str) -> MilestoneRecord | None:
        """Look up a milestone by a PayPal reference, e.g. paypal_capture_id="..."."""
        ((field, value),) = refs.items()
        if field not in UPDATABLE_FIELDS:
            raise ValueError(f"Can't look up by {field}")
        milestone_id = await self._session.scalar(
            select(MilestoneRecord.id).where(getattr(MilestoneRecord, field) == value)
        )
        return await self.get(milestone_id) if milestone_id else None


def _facts_from_updates(updates: dict[str, Any]) -> dict[str, Any]:
    """Translate pending record updates into the MilestoneFacts fields they affect."""
    mapping = {
        "paypal_authorization_id": "authorization_id",
        "paypal_capture_id": "capture_id",
        "authorization_voided": "authorization_voided",
    }
    facts = {mapping[k]: v for k, v in updates.items() if k in mapping}
    if "payout_status" in updates:
        facts["payout_succeeded"] = updates["payout_status"] == "SUCCESS"
    return facts


def _jsonable(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in values.items()}
