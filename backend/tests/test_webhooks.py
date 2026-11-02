import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.paypal.client import PayPalClient
from app.paypal.webhooks import (
    InMemoryEventStore,
    InvalidWebhookError,
    WebhookEvent,
    WebhookOutcome,
    WebhookProcessor,
    WebhookVerifier,
)

SIGNED_HEADERS = {
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT",
    "PAYPAL-TRANSMISSION-ID": "tx-1",
    "PAYPAL-TRANSMISSION-SIG": "sig",
    "PAYPAL-TRANSMISSION-TIME": "2026-11-02T10:00:00Z",
}


def event_body(event_id: str = "WH-1", event_type: str = "PAYMENT.CAPTURE.COMPLETED") -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "event_type": event_type,
            "resource_type": "capture",
            "resource": {"id": "CAP1", "status": "COMPLETED"},
        }
    ).encode()


def mock_verification(router: respx.MockRouter, status: str = "SUCCESS") -> respx.Route:
    return router.post("/v1/notifications/verify-webhook-signature").mock(
        return_value=httpx.Response(200, json={"verification_status": status})
    )


@pytest.fixture
def received() -> list[WebhookEvent]:
    return []


@pytest.fixture
def processor(paypal: PayPalClient, received: list[WebhookEvent]) -> WebhookProcessor:
    async def handler(event: WebhookEvent) -> None:
        received.append(event)

    return WebhookProcessor(
        WebhookVerifier(paypal, "WEBHOOK-ID"),
        InMemoryEventStore(),
        {"PAYMENT.CAPTURE.COMPLETED": handler},
    )


async def test_verified_event_is_dispatched(
    mock_paypal: respx.MockRouter, processor: WebhookProcessor, received: list[WebhookEvent]
) -> None:
    route = mock_verification(mock_paypal)

    outcome = await processor.process(SIGNED_HEADERS, event_body())

    assert outcome is WebhookOutcome.PROCESSED
    assert received[0].resource_id == "CAP1"
    sent = json.loads(route.calls.last.request.content)
    assert sent["webhook_id"] == "WEBHOOK-ID"
    assert sent["transmission_id"] == "tx-1"
    assert sent["webhook_event"]["id"] == "WH-1"


async def test_duplicate_event_is_processed_once(
    mock_paypal: respx.MockRouter, processor: WebhookProcessor, received: list[WebhookEvent]
) -> None:
    mock_verification(mock_paypal)

    first = await processor.process(SIGNED_HEADERS, event_body())
    second = await processor.process(SIGNED_HEADERS, event_body())

    assert (first, second) == (WebhookOutcome.PROCESSED, WebhookOutcome.DUPLICATE)
    assert len(received) == 1


async def test_failed_signature_is_rejected(
    mock_paypal: respx.MockRouter, processor: WebhookProcessor, received: list[WebhookEvent]
) -> None:
    mock_verification(mock_paypal, status="FAILURE")

    with pytest.raises(InvalidWebhookError):
        await processor.process(SIGNED_HEADERS, event_body())
    assert received == []


async def test_missing_headers_rejected_without_calling_paypal(
    mock_paypal: respx.MockRouter, processor: WebhookProcessor
) -> None:
    route = mock_verification(mock_paypal)

    with pytest.raises(InvalidWebhookError):
        await processor.process({}, event_body())
    assert not route.called


async def test_unhandled_event_type_is_ignored(
    mock_paypal: respx.MockRouter, processor: WebhookProcessor
) -> None:
    mock_verification(mock_paypal)

    outcome = await processor.process(
        SIGNED_HEADERS, event_body(event_type="CHECKOUT.ORDER.APPROVED")
    )

    assert outcome is WebhookOutcome.IGNORED


async def test_malformed_body_is_rejected(processor: WebhookProcessor) -> None:
    with pytest.raises(InvalidWebhookError):
        await processor.process(SIGNED_HEADERS, b"not json")


async def test_missing_webhook_id_rejects(
    mock_paypal: respx.MockRouter, paypal: PayPalClient
) -> None:
    verifier = WebhookVerifier(paypal, webhook_id="")
    assert await verifier.verify(SIGNED_HEADERS, json.loads(event_body())) is False


def test_endpoint_returns_400_on_bad_signature(client: TestClient) -> None:
    response = client.post("/webhooks/paypal", content=event_body())  # no signature headers
    assert response.status_code == 400


def test_endpoint_acknowledges_valid_event(client: TestClient) -> None:
    client.app.state.webhook_processor._skip_verification = True

    first = client.post("/webhooks/paypal", content=event_body("WH-9"))
    second = client.post("/webhooks/paypal", content=event_body("WH-9"))

    assert first.json() == {"outcome": "processed"}
    assert second.json() == {"outcome": "duplicate"}
