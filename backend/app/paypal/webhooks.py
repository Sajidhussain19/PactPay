"""PayPal webhook verification, deduplication and dispatch.

PayPal delivers webhooks at-least-once and retries on non-2xx, so the same event can
arrive several times. We (1) verify the signature with PayPal's
verify-webhook-signature API, (2) dedupe by event id, (3) dispatch to a handler.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel

from app.paypal.client import PayPalClient
from app.paypal.errors import PayPalError

logger = logging.getLogger(__name__)

SIGNATURE_HEADERS = {
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}


class EventType(StrEnum):
    AUTHORIZATION_CREATED = "PAYMENT.AUTHORIZATION.CREATED"
    AUTHORIZATION_VOIDED = "PAYMENT.AUTHORIZATION.VOIDED"
    CAPTURE_COMPLETED = "PAYMENT.CAPTURE.COMPLETED"
    CAPTURE_DENIED = "PAYMENT.CAPTURE.DENIED"
    PAYOUTS_ITEM_SUCCEEDED = "PAYMENT.PAYOUTS-ITEM.SUCCEEDED"
    PAYOUTS_ITEM_FAILED = "PAYMENT.PAYOUTS-ITEM.FAILED"
    PAYOUTSBATCH_SUCCESS = "PAYMENT.PAYOUTSBATCH.SUCCESS"


class WebhookEvent(BaseModel):
    id: str
    event_type: str
    resource_type: str | None = None
    resource: dict[str, Any] = {}

    @property
    def resource_id(self) -> str | None:
        return self.resource.get("id") or self.resource.get("payout_item_id")


class WebhookOutcome(StrEnum):
    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"


class InvalidWebhookError(Exception):
    """Signature missing or rejected, or body is not a PayPal event."""


EventHandler = Callable[[WebhookEvent], Awaitable[None]]


class ProcessedEventStore(Protocol):
    async def mark_processed(self, event_id: str) -> bool:
        """Atomically record the event. Return False if it was already recorded."""
        ...


class InMemoryEventStore:
    """Process-local dedupe. Replaced by a DB unique constraint in the persistence module."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._lock = asyncio.Lock()

    async def mark_processed(self, event_id: str) -> bool:
        async with self._lock:
            if event_id in self._seen:
                return False
            self._seen.add(event_id)
            return True


class WebhookVerifier:
    def __init__(self, client: PayPalClient, webhook_id: str) -> None:
        self._client = client
        self._webhook_id = webhook_id

    async def verify(self, headers: Mapping[str, str], event: dict[str, Any]) -> bool:
        if not self._webhook_id:
            logger.error("PAYPAL_WEBHOOK_ID is not set; rejecting webhook")
            return False
        lowered = {k.lower(): v for k, v in headers.items()}
        fields = {name: lowered.get(header) for name, header in SIGNATURE_HEADERS.items()}
        if not all(fields.values()):
            return False
        try:
            result = await self._client.request(
                "POST",
                "/v1/notifications/verify-webhook-signature",
                json={**fields, "webhook_id": self._webhook_id, "webhook_event": event},
                prefer_representation=False,
            )
        except PayPalError:
            logger.exception("verify-webhook-signature call failed")
            return False
        return result.get("verification_status") == "SUCCESS"


class WebhookProcessor:
    def __init__(
        self,
        verifier: WebhookVerifier,
        store: ProcessedEventStore,
        handlers: Mapping[str, EventHandler] | None = None,
        *,
        skip_verification: bool = False,
    ) -> None:
        self._verifier = verifier
        self._store = store
        self._handlers = dict(handlers or {})
        self._skip_verification = skip_verification

    def register(self, event_type: str, handler: EventHandler) -> None:
        self._handlers[event_type] = handler

    async def process(self, headers: Mapping[str, str], raw_body: bytes) -> WebhookOutcome:
        try:
            payload = json.loads(raw_body)
            event = WebhookEvent.model_validate(payload)
        except (ValueError, TypeError) as exc:
            raise InvalidWebhookError("Body is not a valid PayPal webhook event") from exc

        if self._skip_verification:
            logger.warning("Webhook signature verification SKIPPED (dev only) for %s", event.id)
        elif not await self._verifier.verify(headers, payload):
            raise InvalidWebhookError(f"Signature verification failed for event {event.id}")

        if not await self._store.mark_processed(event.id):
            logger.info("Duplicate webhook %s (%s) ignored", event.id, event.event_type)
            return WebhookOutcome.DUPLICATE

        handler = self._handlers.get(event.event_type)
        if handler is None:
            logger.info("No handler for webhook %s (%s)", event.id, event.event_type)
            return WebhookOutcome.IGNORED

        await handler(event)
        logger.info(
            "Processed webhook %s %s resource=%s", event.id, event.event_type, event.resource_id
        )
        return WebhookOutcome.PROCESSED


async def log_event(event: WebhookEvent) -> None:
    """Default handler until the state machine is wired in (persistence module)."""
    logger.info(
        "PayPal event %s: resource=%s status=%s",
        event.event_type,
        event.resource_id,
        event.resource.get("status") or event.resource.get("transaction_status"),
    )


DEFAULT_HANDLERS: dict[str, EventHandler] = {
    event_type.value: log_event for event_type in EventType
}
