import os
import ssl
import tempfile
import threading
from collections.abc import AsyncIterator, Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx
from fastapi.testclient import TestClient

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ARTIFACTS_DIR"] = tempfile.mkdtemp(prefix="pactpay-test-artifacts-")
os.environ["AUTO_APPROVE_CHECK_SECONDS"] = "0"  # no background scheduler in tests
os.environ["DEMO_MODE"] = "true"
os.environ["DEMO_FREELANCER_PAYPAL_EMAIL"] = "freelancer-sandbox@personal.example.com"
# Environment variables beat backend/.env, so tests never use real keys or spend credits.
for _secret in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_WEBHOOK_ID", "OPENAI_API_KEY"):
    os.environ[_secret] = ""

from app.paypal.client import PayPalClient  # noqa: E402
from app.verification.web_checks import WebChecker  # noqa: E402
from tests.paypal_mocks import BASE_URL, no_sleep, token_response  # noqa: E402

# Building an SSL context is slow (~1s on Windows); share one across tests.
_SSL_CONTEXT = ssl.create_default_context()


def auth_headers(client: TestClient, role: str) -> dict[str, str]:
    token = client.post("/auth/demo-login", json={"role": role}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """App with its own SQLite *file*. In-memory SQLite shares one connection across
    sessions, which breaks when background tasks and requests overlap."""
    from app.core.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:  # `with` runs the lifespan
            yield test_client
    finally:
        get_settings.cache_clear()


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


DEMO_DIR = Path(__file__).resolve().parents[2] / "demo"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture(scope="session")
def demo_url() -> Iterator[str]:
    """Serves demo/ (good, buggy and malicious sites) on a random local port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=DEMO_DIR))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def web(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[WebChecker]:
    """One Chromium for the whole session; each check still gets an isolated context."""
    async with WebChecker(tmp_path_factory.mktemp("artifacts"), timeout_seconds=10) as checker:
        yield checker
