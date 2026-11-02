import json
from decimal import Decimal

import httpx
import pytest
import respx

from app.paypal.client import PayPalClient
from app.paypal.payouts import PayoutsService, split_amount


@pytest.mark.parametrize(
    ("gross", "percent", "fee", "net"),
    [
        ("150.00", "5", "7.50", "142.50"),
        ("100", "0", "0.00", "100.00"),
        ("10.01", "2.5", "0.25", "9.76"),  # 0.25025 rounds half-up to 0.25
        ("0.99", "5", "0.05", "0.94"),  # 0.0495 rounds half-up to 0.05
    ],
)
def test_split_amount_adds_up(gross: str, percent: str, fee: str, net: str) -> None:
    split = split_amount(gross, percent)
    assert split.fee == Decimal(fee)
    assert split.net == Decimal(net)
    assert split.fee + split.net == split.gross


@pytest.mark.parametrize(
    ("gross", "percent"), [("0", "5"), ("-1", "5"), ("10", "100"), ("10", "-1")]
)
def test_split_amount_rejects_invalid(gross: str, percent: str) -> None:
    with pytest.raises(ValueError):
        split_amount(gross, percent)


async def test_pay_freelancer_uses_sender_batch_id_for_idempotency(
    mock_paypal: respx.MockRouter, paypal: PayPalClient
) -> None:
    route = mock_paypal.post("/v1/payments/payouts").mock(
        return_value=httpx.Response(
            201,
            json={"batch_header": {"payout_batch_id": "BATCH1", "batch_status": "PENDING"}},
        )
    )

    batch = await PayoutsService(paypal).pay_freelancer(
        receiver_email="freelancer@example.com",
        amount=Decimal("142.50"),
        currency="USD",
        sender_batch_id="payout-m1",
    )

    request = route.calls.last.request
    body = json.loads(request.content)
    assert body["sender_batch_header"]["sender_batch_id"] == "payout-m1"
    assert body["items"][0]["amount"] == {"value": "142.50", "currency": "USD"}
    assert request.headers["PayPal-Request-Id"] == "payout-m1"
    assert batch.payout_batch_id == "BATCH1"


async def test_get_payout_batch_parses_items(
    mock_paypal: respx.MockRouter, paypal: PayPalClient
) -> None:
    mock_paypal.get("/v1/payments/payouts/BATCH1").mock(
        return_value=httpx.Response(
            200,
            json={
                "batch_header": {"payout_batch_id": "BATCH1", "batch_status": "SUCCESS"},
                "items": [
                    {
                        "payout_item_id": "ITEM1",
                        "transaction_id": "TX1",
                        "transaction_status": "SUCCESS",
                        "payout_item": {"sender_item_id": "payout-m1-item"},
                    }
                ],
            },
        )
    )

    batch = await PayoutsService(paypal).get_payout_batch("BATCH1")

    assert batch.batch_status == "SUCCESS"
    assert batch.items[0].transaction_status == "SUCCESS"
    assert batch.items[0].sender_item_id == "payout-m1-item"
