"""PayPal Orders v2 + Payments v2 for the authorize-then-capture escrow flow.

Flow per milestone:
  create_authorize_order -> buyer approves at `approve_url` -> authorize_order (funds held)
  -> capture_authorization (release) | void_authorization (refund)
  reauthorize_authorization renews a hold after the honor period on long milestones.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.core.money import to_money, to_paypal_value
from app.paypal.client import PayPalClient
from app.paypal.errors import PayPalError


class Money(BaseModel):
    currency_code: str
    value: Decimal


class CreatedOrder(BaseModel):
    id: str
    status: str
    approve_url: str


class Authorization(BaseModel):
    id: str
    status: str  # CREATED, CAPTURED, PARTIALLY_CAPTURED, VOIDED, EXPIRED, DENIED, PENDING
    amount: Money | None = None
    expiration_time: datetime | None = None


class AuthorizedOrder(BaseModel):
    order_id: str
    order_status: str
    authorization: Authorization


class Capture(BaseModel):
    id: str
    status: str  # COMPLETED, PENDING, DECLINED, ...
    amount: Money | None = None


class OrdersService:
    def __init__(self, client: PayPalClient) -> None:
        self._client = client

    async def create_authorize_order(
        self,
        *,
        amount: Decimal | str,
        currency: str,
        reference_id: str,
        description: str,
        return_url: str,
        cancel_url: str,
        request_id: str | None = None,
    ) -> CreatedOrder:
        body = {
            "intent": "AUTHORIZE",
            "purchase_units": [
                {
                    "reference_id": reference_id,
                    "custom_id": reference_id,
                    "description": description[:127],
                    "amount": {"currency_code": currency, "value": to_paypal_value(amount)},
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "brand_name": "PactPay",
                        "user_action": "PAY_NOW",
                        "shipping_preference": "NO_SHIPPING",
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                    }
                }
            },
        }
        data = await self._client.request(
            "POST", "/v2/checkout/orders", json=body, request_id=request_id
        )
        return CreatedOrder(id=data["id"], status=data["status"], approve_url=_approve_url(data))

    async def get_order(self, order_id: str) -> dict[str, Any]:
        return await self._client.request("GET", f"/v2/checkout/orders/{order_id}")

    async def authorize_order(
        self, order_id: str, *, request_id: str | None = None
    ) -> AuthorizedOrder:
        """Place the hold. Only valid after the buyer approved (order status APPROVED)."""
        data = await self._client.request(
            "POST",
            f"/v2/checkout/orders/{order_id}/authorize",
            json={},
            request_id=request_id or f"authorize-{order_id}",
        )
        try:
            raw_auth = data["purchase_units"][0]["payments"]["authorizations"][0]
        except (KeyError, IndexError) as exc:
            raise PayPalError(
                f"Authorize response for order {order_id} had no authorization"
            ) from exc
        return AuthorizedOrder(
            order_id=data["id"], order_status=data["status"], authorization=_authorization(raw_auth)
        )

    async def get_authorization(self, authorization_id: str) -> Authorization:
        data = await self._client.request("GET", f"/v2/payments/authorizations/{authorization_id}")
        return _authorization(data)

    async def capture_authorization(
        self,
        authorization_id: str,
        *,
        request_id: str,
        amount: Decimal | str | None = None,
        currency: str | None = None,
        invoice_id: str | None = None,
        final_capture: bool = True,
    ) -> Capture:
        """Move held funds to the platform. `request_id` is required: double capture is the
        worst bug this system can have."""
        body: dict[str, Any] = {"final_capture": final_capture}
        if amount is not None:
            if currency is None:
                raise ValueError("currency is required for a partial capture amount")
            body["amount"] = {"currency_code": currency, "value": to_paypal_value(amount)}
        if invoice_id:
            body["invoice_id"] = invoice_id
        data = await self._client.request(
            "POST",
            f"/v2/payments/authorizations/{authorization_id}/capture",
            json=body,
            request_id=request_id,
        )
        return Capture(id=data["id"], status=data["status"], amount=_money(data.get("amount")))

    async def void_authorization(self, authorization_id: str, *, request_id: str) -> None:
        """Release the hold back to the buyer. Returns 204 (or 200 with representation)."""
        await self._client.request(
            "POST",
            f"/v2/payments/authorizations/{authorization_id}/void",
            request_id=request_id,
        )

    async def reauthorize_authorization(
        self,
        authorization_id: str,
        *,
        request_id: str,
        amount: Decimal | str | None = None,
        currency: str | None = None,
    ) -> Authorization:
        """Renew a hold after the honor period. Returns a NEW authorization id."""
        body: dict[str, Any] = {}
        if amount is not None and currency is not None:
            body["amount"] = {"currency_code": currency, "value": to_paypal_value(amount)}
        data = await self._client.request(
            "POST",
            f"/v2/payments/authorizations/{authorization_id}/reauthorize",
            json=body,
            request_id=request_id,
        )
        return _authorization(data)


def _approve_url(order: dict[str, Any]) -> str:
    # With payment_source.paypal the buyer link is "payer-action"; older flows use "approve".
    for link in order.get("links", []):
        if link.get("rel") in {"payer-action", "approve"}:
            return link["href"]
    raise PayPalError(f"Order {order.get('id')} has no approval link")


def _money(raw: dict[str, Any] | None) -> Money | None:
    if not raw:
        return None
    return Money(currency_code=raw["currency_code"], value=to_money(raw["value"]))


def _authorization(raw: dict[str, Any]) -> Authorization:
    return Authorization(
        id=raw["id"],
        status=raw["status"],
        amount=_money(raw.get("amount")),
        expiration_time=raw.get("expiration_time"),
    )
