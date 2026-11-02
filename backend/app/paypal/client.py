"""Async PayPal REST client.

Responsibilities:
- OAuth2 client-credentials token, cached until shortly before expiry, refreshed on 401.
- Retries with exponential backoff + jitter on 5xx, 429 and network errors.
- The same PayPal-Request-Id is sent on every retry, so PayPal deduplicates the
  operation instead of executing it twice.
- Logs PayPal-Debug-Id for every response so failures can be traced with PayPal support.
"""

import asyncio
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Self

import httpx

from app.core.config import Settings
from app.paypal.errors import PayPalAuthError, PayPalError, PayPalNotConfiguredError

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
TOKEN_EXPIRY_MARGIN_SECONDS = 60


class PayPalClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str,
        *,
        http: httpx.AsyncClient | None = None,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        timeout: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.AsyncClient(base_url=base_url, timeout=timeout)
        self._owns_http = http is None
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> Self:
        return cls(
            settings.paypal_client_id,
            settings.paypal_client_secret.get_secret_value(),
            settings.paypal_base_url,
            **kwargs,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ------------------------------------------------------------------ auth

    async def get_access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        async with self._token_lock:
            # Another coroutine may have refreshed while we waited for the lock.
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token
            await self._fetch_token()
            assert self._token is not None
            return self._token

    def invalidate_token(self) -> None:
        self._token = None
        self._token_expires_at = 0.0

    async def _fetch_token(self) -> None:
        if not self._client_id or not self._client_secret:
            raise PayPalNotConfiguredError(
                "PayPal credentials missing: set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET"
            )
        response = await self._send_with_retries(
            "POST",
            "/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            body = _safe_json(response)
            raise PayPalAuthError(
                body.get("error_description", "PayPal OAuth token request failed"),
                status_code=response.status_code,
                name=body.get("error"),
                debug_id=response.headers.get("paypal-debug-id"),
            )
        body = response.json()
        expires_in = int(body.get("expires_in", 0))
        self._token = body["access_token"]
        self._token_expires_at = time.monotonic() + max(expires_in - TOKEN_EXPIRY_MARGIN_SECONDS, 0)
        logger.info("PayPal access token refreshed (expires_in=%ss)", expires_in)

    # -------------------------------------------------------------- requests

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
        prefer_representation: bool = True,
    ) -> dict[str, Any]:
        """Call a PayPal API and return the parsed JSON body ({} for 204).

        Pass a stable `request_id` for anything that moves money, derived from our own
        identifiers (e.g. f"capture-{milestone_id}"), so a retry after a crash is safe.
        """
        headers = {
            "Content-Type": "application/json",
            "PayPal-Request-Id": request_id or str(uuid.uuid4()),
        }
        if prefer_representation:
            headers["Prefer"] = "return=representation"

        refreshed = False
        while True:
            token = await self.get_access_token()
            headers["Authorization"] = f"Bearer {token}"
            response = await self._send_with_retries(
                method, path, json=json, params=params, headers=headers
            )
            if response.status_code == 401 and not refreshed:
                # Token revoked or expired early; refresh once and retry.
                self.invalidate_token()
                refreshed = True
                continue
            break

        if response.is_success:
            return _safe_json(response)

        body = _safe_json(response)
        raise PayPalError(
            body.get("message") or f"PayPal {method} {path} failed",
            status_code=response.status_code,
            name=body.get("name"),
            debug_id=body.get("debug_id") or response.headers.get("paypal-debug-id"),
            details=body.get("details"),
        )

    async def _send_with_retries(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        attempt = 0
        while True:
            try:
                response = await self._http.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise PayPalError(
                        f"Network error calling PayPal {method} {path}: {exc}"
                    ) from exc
                logger.warning("PayPal %s %s network error (%s), retrying", method, path, exc)
            else:
                logger.info(
                    "PayPal %s %s -> %s debug_id=%s",
                    method,
                    path,
                    response.status_code,
                    response.headers.get("paypal-debug-id", "-"),
                )
                if response.status_code not in RETRYABLE_STATUS or attempt >= self._max_retries:
                    return response
            attempt += 1
            await self._sleep(self._backoff_delay(attempt))

    def _backoff_delay(self, attempt: int) -> float:
        delay = self._backoff_base * (2 ** (attempt - 1))
        return delay + random.uniform(0, delay / 2)  # noqa: S311 - jitter, not crypto


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    if not response.content:
        return {}
    try:
        data = response.json()
    except ValueError:
        return {"message": response.text[:500]}
    return data if isinstance(data, dict) else {"data": data}
