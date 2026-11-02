"""Run the full PactPay money flow against the PayPal sandbox.

    cd backend
    python -m scripts.sandbox_flow --amount 150 --freelancer-email <sandbox personal email>
    python -m scripts.sandbox_flow --amount 150 --void          # test the refund path

Steps: create AUTHORIZE order -> you approve in the browser as the sandbox client ->
authorize (funds held) -> capture -> payout (minus platform fee) -> payout status.
"""

import argparse
import asyncio
import sys
import uuid
from decimal import Decimal

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.paypal.client import PayPalClient
from app.paypal.errors import PayPalError
from app.paypal.orders import OrdersService
from app.paypal.payouts import PayoutsService, split_amount

PAYOUT_POLL_ATTEMPTS = 6
PAYOUT_POLL_SECONDS = 5


def step(title: str) -> None:
    print(f"\n=== {title} ===")


async def run(amount: Decimal, freelancer_email: str | None, void: bool) -> None:
    settings = get_settings()
    milestone_id = f"demo-{uuid.uuid4().hex[:8]}"
    currency = settings.paypal_currency

    async with PayPalClient.from_settings(settings) as paypal:
        orders = OrdersService(paypal)

        step(f"1. Create AUTHORIZE order for {amount} {currency} (milestone {milestone_id})")
        order = await orders.create_authorize_order(
            amount=amount,
            currency=currency,
            reference_id=milestone_id,
            description="PactPay sandbox demo milestone",
            return_url=f"{settings.frontend_url}/paypal/return",
            cancel_url=f"{settings.frontend_url}/paypal/cancel",
            request_id=f"order-{milestone_id}",
        )
        print(f"order_id={order.id} status={order.status}")
        print(
            f"\nOpen this URL, log in as your sandbox CLIENT (personal) account and approve:\n"
            f"  {order.approve_url}\n"
            "After approving you'll land on a return URL that may not load yet. That's fine."
        )
        await asyncio.to_thread(input, "Press Enter once approved... ")

        step("2. Authorize (funds held, not taken)")
        authorized = await orders.authorize_order(order.id, request_id=f"authorize-{milestone_id}")
        auth = authorized.authorization
        print(f"authorization_id={auth.id} status={auth.status} expires={auth.expiration_time}")

        if void:
            step("3. Void authorization (client refunded)")
            await orders.void_authorization(auth.id, request_id=f"void-{milestone_id}")
            print(f"status={(await orders.get_authorization(auth.id)).status}")
            return

        step("3. Capture authorization (release)")
        capture = await orders.capture_authorization(auth.id, request_id=f"capture-{milestone_id}")
        print(f"capture_id={capture.id} status={capture.status}")

        if not freelancer_email:
            print("\nNo --freelancer-email given; skipping payout.")
            return

        split = split_amount(amount, settings.platform_fee_percent)
        step(f"4. Payout {split.net} to {freelancer_email} (fee {split.fee})")
        payouts = PayoutsService(paypal)
        batch = await payouts.pay_freelancer(
            receiver_email=freelancer_email,
            amount=split.net,
            currency=currency,
            sender_batch_id=f"payout-{milestone_id}",
        )
        print(f"payout_batch_id={batch.payout_batch_id} status={batch.batch_status}")

        step("5. Payout status")
        for _ in range(PAYOUT_POLL_ATTEMPTS):
            batch = await payouts.get_payout_batch(batch.payout_batch_id)
            item_statuses = [i.transaction_status for i in batch.items]
            print(f"batch_status={batch.batch_status} items={item_statuses}")
            if batch.batch_status in {"SUCCESS", "DENIED"}:
                break
            await asyncio.sleep(PAYOUT_POLL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--amount", type=Decimal, default=Decimal("150"))
    parser.add_argument("--freelancer-email", help="Sandbox personal account email for payout")
    parser.add_argument("--void", action="store_true", help="Void instead of capture")
    args = parser.parse_args()

    configure_logging("WARNING")
    try:
        asyncio.run(run(args.amount, args.freelancer_email, args.void))
    except PayPalError as exc:
        print(f"\nPayPal error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
