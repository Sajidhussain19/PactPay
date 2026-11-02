import httpx
import pytest
import respx

from app.paypal.client import PayPalClient
from app.paypal.errors import PayPalAuthError, PayPalError, PayPalNotConfiguredError
from tests.paypal_mocks import BASE_URL, token_response


async def test_token_is_cached_across_requests(
    mock_paypal: respx.MockRouter, paypal: PayPalClient
) -> None:
    mock_paypal.get("/v2/checkout/orders/O1").mock(
        return_value=httpx.Response(200, json={"id": "O1"})
    )

    await paypal.request("GET", "/v2/checkout/orders/O1")
    await paypal.request("GET", "/v2/checkout/orders/O1")

    assert mock_paypal.routes[0].call_count == 1  # one OAuth call for two API calls


async def test_bearer_token_and_request_id_are_sent(
    mock_paypal: respx.MockRouter, paypal: PayPalClient
) -> None:
    route = mock_paypal.post("/v2/payments/authorizations/A1/capture").mock(
        return_value=httpx.Response(201, json={"id": "C1", "status": "COMPLETED"})
    )

    await paypal.request(
        "POST", "/v2/payments/authorizations/A1/capture", json={}, request_id="capture-m1"
    )

    sent = route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer tok-1"
    assert sent.headers["PayPal-Request-Id"] == "capture-m1"


async def test_retries_5xx_with_same_request_id(
    mock_paypal: respx.MockRouter, paypal: PayPalClient
) -> None:
    route = mock_paypal.post("/v2/checkout/orders").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(500),
            httpx.Response(201, json={"id": "O1", "status": "CREATED"}),
        ]
    )

    result = await paypal.request("POST", "/v2/checkout/orders", json={}, request_id="order-m1")

    assert result["id"] == "O1"
    assert route.call_count == 3
    request_ids = {call.request.headers["PayPal-Request-Id"] for call in route.calls}
    assert request_ids == {"order-m1"}


async def test_retries_network_errors(mock_paypal: respx.MockRouter, paypal: PayPalClient) -> None:
    route = mock_paypal.get("/v2/checkout/orders/O1").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json={"id": "O1"})]
    )

    assert (await paypal.request("GET", "/v2/checkout/orders/O1"))["id"] == "O1"
    assert route.call_count == 2


async def test_gives_up_after_max_retries(
    mock_paypal: respx.MockRouter, paypal: PayPalClient
) -> None:
    route = mock_paypal.get("/v2/checkout/orders/O1").mock(return_value=httpx.Response(503))

    with pytest.raises(PayPalError) as exc_info:
        await paypal.request("GET", "/v2/checkout/orders/O1")

    assert exc_info.value.status_code == 503
    assert route.call_count == 4  # 1 attempt + 3 retries


async def test_4xx_is_not_retried_and_error_is_parsed(
    mock_paypal: respx.MockRouter, paypal: PayPalClient
) -> None:
    route = mock_paypal.post("/v2/payments/authorizations/A1/capture").mock(
        return_value=httpx.Response(
            422,
            json={
                "name": "UNPROCESSABLE_ENTITY",
                "message": "The requested action could not be performed.",
                "debug_id": "dbg-123",
                "details": [{"issue": "AUTHORIZATION_EXPIRED", "description": "expired"}],
            },
        )
    )

    with pytest.raises(PayPalError) as exc_info:
        await paypal.request("POST", "/v2/payments/authorizations/A1/capture", request_id="x")

    error = exc_info.value
    assert route.call_count == 1
    assert error.debug_id == "dbg-123"
    assert error.has_issue("AUTHORIZATION_EXPIRED")


async def test_401_refreshes_token_once(
    mock_paypal: respx.MockRouter, paypal: PayPalClient
) -> None:
    mock_paypal.routes[0].mock(side_effect=[token_response("old"), token_response("new")])
    route = mock_paypal.get("/v2/checkout/orders/O1").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json={"id": "O1"})]
    )

    await paypal.request("GET", "/v2/checkout/orders/O1")

    assert [c.request.headers["Authorization"] for c in route.calls] == [
        "Bearer old",
        "Bearer new",
    ]


async def test_bad_credentials_raise_auth_error(
    mock_paypal: respx.MockRouter, paypal: PayPalClient
) -> None:
    mock_paypal.routes[0].mock(
        return_value=httpx.Response(
            401,
            json={"error": "invalid_client", "error_description": "Client Authentication failed"},
        )
    )

    with pytest.raises(PayPalAuthError, match="Client Authentication failed"):
        await paypal.get_access_token()


async def test_missing_credentials_raise_not_configured() -> None:
    client = PayPalClient("", "", BASE_URL)
    with pytest.raises(PayPalNotConfiguredError):
        await client.get_access_token()
    await client.aclose()
