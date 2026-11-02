"""PayPal Payouts: send the freelancer their share after capture.

Idempotency: PayPal rejects a reused `sender_batch_id` (within 30 days), so we derive it
from the milestone. A retry after a crash therefore can never pay the freelancer twice.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel

from app.core.money import CENT, to_money, to_paypal_value
from app.paypal.client import PayPalClient


class PayoutSplit(BaseModel):
    gross: Decimal
    fee: Decimal
    net: Decimal


def split_amount(gross: Decimal | str, fee_percent: Decimal | str) -> PayoutSplit:
    """Split a captured amount into platform fee and freelancer net.

    The fee is rounded half-up to the cent; net = gross - fee so the parts always add up.
    """
    gross_amount = to_money(gross)
    percent = Decimal(str(fee_percent))
    if gross_amount <= 0:
        raise ValueError("gross must be positive")
    if not Decimal(0) <= percent < Decimal(100):
        raise ValueError("fee_percent must be in [0, 100)")
    fee = (gross_amount * percent / Decimal(100)).quantize(CENT, rounding=ROUND_HALF_UP)
    return PayoutSplit(gross=gross_amount, fee=fee, net=gross_amount - fee)


class PayoutItemStatus(BaseModel):
    payout_item_id: str
    transaction_status: str | None = None  # SUCCESS, PENDING, UNCLAIMED, FAILED, ...
    transaction_id: str | None = None
    sender_item_id: str | None = None
    errors: dict[str, Any] | None = None


class PayoutBatch(BaseModel):
    payout_batch_id: str
    batch_status: str  # PENDING, PROCESSING, SUCCESS, DENIED, ...
    items: list[PayoutItemStatus] = []


class PayoutsService:
    def __init__(self, client: PayPalClient) -> None:
        self._client = client

    async def pay_freelancer(
        self,
        *,
        receiver_email: str,
        amount: Decimal | str,
        currency: str,
        sender_batch_id: str,
        note: str = "Payment released by PactPay",
    ) -> PayoutBatch:
        body = {
            "sender_batch_header": {
                "sender_batch_id": sender_batch_id,
                "email_subject": "You have been paid via PactPay",
                "email_message": note,
            },
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "receiver": receiver_email,
                    "amount": {"value": to_paypal_value(amount), "currency": currency},
                    "note": note,
                    "sender_item_id": f"{sender_batch_id}-item",
                }
            ],
        }
        data = await self._client.request(
            "POST",
            "/v1/payments/payouts",
            json=body,
            request_id=sender_batch_id,
            prefer_representation=False,
        )
        return _batch(data)

    async def get_payout_batch(self, payout_batch_id: str) -> PayoutBatch:
        data = await self._client.request(
            "GET", f"/v1/payments/payouts/{payout_batch_id}", prefer_representation=False
        )
        return _batch(data)


def _batch(data: dict[str, Any]) -> PayoutBatch:
    header = data["batch_header"]
    items = [
        PayoutItemStatus(
            payout_item_id=item["payout_item_id"],
            transaction_status=item.get("transaction_status"),
            transaction_id=item.get("transaction_id"),
            sender_item_id=item.get("payout_item", {}).get("sender_item_id"),
            errors=item.get("errors"),
        )
        for item in data.get("items", [])
    ]
    return PayoutBatch(
        payout_batch_id=header["payout_batch_id"], batch_status=header["batch_status"], items=items
    )
