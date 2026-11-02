import json
from decimal import Decimal

import httpx
import pytest
import respx

from app.paypal.client import PayPalClient
from app.paypal.errors import PayPalError
from app.paypal.orders import OrdersService


@pytest.fixture
def orders(paypal: PayPalClient) -> OrdersService:
    return OrdersService(paypal)


async def test_create_authorize_order_builds_body_and_returns_approve_url(
    mock_paypal: respx.MockRouter, orders: OrdersService
) -> None:
    route = mock_paypal.post("/v2/checkout/orders").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "ORDER1",
                "status": "PAYER_ACTION_REQUIRED",
                "links": [
                    {"rel": "self", "href": "https://api/self"},
                    {"rel": "payer-action", "href": "https://paypal.test/checkoutnow?token=ORDER1"},
                ],
            },
        )
    )

    order = await orders.create_authorize_order(
        amount=Decimal("150"),
        currency="USD",
        reference_id="milestone-1",
        description="Bakery landing page",
        return_url="http://localhost:3000/return",
        cancel_url="http://localhost:3000/cancel",
    )

    body = json.loads(route.calls.last.request.content)
    assert body["intent"] == "AUTHORIZE"
    assert body["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "150.00"}
    assert body["purchase_units"][0]["reference_id"] == "milestone-1"
    assert order.approve_url.endswith("token=ORDER1")


async def test_create_order_rejects_non_positive_amount(orders: OrdersService) -> None:
    with pytest.raises(ValueError):
        await orders.create_authorize_order(
            amount="0",
            currency="USD",
            reference_id="m",
            description="d",
            return_url="r",
            cancel_url="c",
        )


async def test_authorize_order_extracts_authorization(
    mock_paypal: respx.MockRouter, orders: OrdersService
) -> None:
    route = mock_paypal.post("/v2/checkout/orders/ORDER1/authorize").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "ORDER1",
                "status": "COMPLETED",
                "purchase_units": [
                    {
                        "payments": {
                            "authorizations": [
                                {
                                    "id": "AUTH1",
                                    "status": "CREATED",
                                    "amount": {"currency_code": "USD", "value": "150.00"},
                                    "expiration_time": "2026-12-01T10:00:00Z",
                                }
                            ]
                        }
                    }
                ],
            },
        )
    )

    result = await orders.authorize_order("ORDER1")

    assert result.authorization.id == "AUTH1"
    assert result.authorization.status == "CREATED"
    assert result.authorization.amount is not None
    assert result.authorization.amount.value == Decimal("150.00")
    # Default request id is derived from the order, so retries are idempotent.
    assert route.calls.last.request.headers["PayPal-Request-Id"] == "authorize-ORDER1"


async def test_authorize_without_authorization_raises(
    mock_paypal: respx.MockRouter, orders: OrdersService
) -> None:
    mock_paypal.post("/v2/checkout/orders/ORDER1/authorize").mock(
        return_value=httpx.Response(201, json={"id": "ORDER1", "status": "COMPLETED"})
    )
    with pytest.raises(PayPalError):
        await orders.authorize_order("ORDER1")


async def test_capture_authorization_final_capture(
    mock_paypal: respx.MockRouter, orders: OrdersService
) -> None:
    route = mock_paypal.post("/v2/payments/authorizations/AUTH1/capture").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "CAP1",
                "status": "COMPLETED",
                "amount": {"currency_code": "USD", "value": "150.00"},
            },
        )
    )

    capture = await orders.capture_authorization("AUTH1", request_id="capture-m1")

    assert capture.id == "CAP1"
    assert capture.status == "COMPLETED"
    assert json.loads(route.calls.last.request.content) == {"final_capture": True}


async def test_partial_capture_requires_currency(orders: OrdersService) -> None:
    with pytest.raises(ValueError, match="currency"):
        await orders.capture_authorization("AUTH1", request_id="x", amount="50")


async def test_void_authorization_handles_204(
    mock_paypal: respx.MockRouter, orders: OrdersService
) -> None:
    route = mock_paypal.post("/v2/payments/authorizations/AUTH1/void").mock(
        return_value=httpx.Response(204)
    )

    await orders.void_authorization("AUTH1", request_id="void-m1")

    assert route.called


async def test_reauthorize_returns_new_authorization(
    mock_paypal: respx.MockRouter, orders: OrdersService
) -> None:
    mock_paypal.post("/v2/payments/authorizations/AUTH1/reauthorize").mock(
        return_value=httpx.Response(201, json={"id": "AUTH2", "status": "CREATED"})
    )

    auth = await orders.reauthorize_authorization("AUTH1", request_id="reauth-m1")

    assert auth.id == "AUTH2"
