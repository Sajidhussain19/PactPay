import os
import ssl
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "test")

from app.paypal.client import PayPalClient  # noqa: E402
from tests.paypal_mocks import BASE_URL, no_sleep, token_response  # noqa: E402

# Building an SSL context is slow (~1s on Windows); share one across tests.
_SSL_CONTEXT = ssl.create_default_context()


@pytest.fixture
def client() -> Iterator[TestClient]:
    from app.main import create_app

    with TestClient(create_app()) as test_client:  # `with` runs the lifespan
        yield test_client


@pytest.fixture
def mock_paypal() -> Iterator[respx.MockRouter]:
    """Mocked PayPal API. routes[0] is the OAuth token endpoint."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.post("/v1/oauth2/token").mock(return_value=token_response())
        yield router


@pytest.fixture
async def paypal(mock_paypal: respx.MockRouter) -> AsyncIterator[PayPalClient]:
    async with PayPalClient(
        "client-id",
        "client-secret",
        BASE_URL,
        http=httpx.AsyncClient(base_url=BASE_URL, verify=_SSL_CONTEXT),
        backoff_base=0,
        sleep=no_sleep,
    ) as client:
        yield client
