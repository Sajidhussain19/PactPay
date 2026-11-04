"""End-to-end API flow with fake PayPal and fake checks.

Everything else is real: HTTP endpoints, auth, state machine, database, background
verification, SSE. Fakes count calls so we can prove money is never moved twice.
"""

import json
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.agents.contract_schema import Criterion
from app.agents.judge import RuleJudge
from app.models.db import MilestoneRecord, PaymentEvent, utcnow
from app.paypal.errors import PayPalError
from app.paypal.orders import Authorization, AuthorizedOrder, Capture, CreatedOrder
from app.paypal.payouts import PayoutBatch, PayoutItemStatus
from app.paypal.webhooks import InMemoryEventStore, WebhookProcessor
from app.services.payments import PaymentRuntime
from app.services.verification_manager import VerificationManager
from app.verification.models import CheckResult
from app.verification.report import Submission
from app.workflows.verification_graph import CheckFn
from tests.conftest import DEMO_DIR, auth_headers

BAKERY = json.loads((DEMO_DIR / "contracts" / "bakery.json").read_text())


@dataclass
class FakeOrders:
    calls: Counter[str] = field(default_factory=Counter)
    request_ids: list[str] = field(default_factory=list)

    async def create_authorize_order(self, **kwargs: Any) -> CreatedOrder:
        self.calls["create"] += 1
        return CreatedOrder(
            id="ORDER-1",
            status="PAYER_ACTION_REQUIRED",
            approve_url="https://www.sandbox.paypal.com/checkoutnow?token=ORDER-1",
        )

    async def authorize_order(
        self, order_id: str, *, request_id: str | None = None
    ) -> AuthorizedOrder:
        self.calls["authorize"] += 1
        return AuthorizedOrder(
            order_id=order_id,
            order_status="COMPLETED",
            authorization=Authorization(id="AUTH-1", status="CREATED"),
        )

    async def create_sandbox_card_authorization(self, **kwargs: Any) -> AuthorizedOrder:
        self.calls["card"] += 1
        return AuthorizedOrder(
            order_id="ORDER-CARD",
            order_status="COMPLETED",
            authorization=Authorization(id="AUTH-CARD", status="CREATED"),
        )

    async def capture_authorization(self, authorization_id: str, *, request_id: str) -> Capture:
        self.calls["capture"] += 1
        self.request_ids.append(request_id)
        return Capture(id="CAP-1", status="COMPLETED")

    async def void_authorization(self, authorization_id: str, *, request_id: str) -> None:
        self.calls["void"] += 1

    async def reauthorize_authorization(
        self, authorization_id: str, *, request_id: str
    ) -> Authorization:
        self.calls["reauthorize"] += 1
        return Authorization(id="AUTH-2", status="CREATED")


@dataclass
class FakePayouts:
    fail_next: int = 0
    settle_as: str = "SUCCESS"
    sent: list[dict[str, Any]] = field(default_factory=list)

    async def pay_freelancer(self, **kwargs: Any) -> PayoutBatch:
        if self.fail_next:
            self.fail_next -= 1
            raise PayPalError(
                "You live in a country that is not allowed to send this payout.",
                status_code=403,
                name="PAYOUT_NOT_AVAILABLE",
            )
        self.sent.append(kwargs)
        return PayoutBatch(payout_batch_id="BATCH-1", batch_status="PENDING")

    async def get_payout_batch(self, payout_batch_id: str) -> PayoutBatch:
        return PayoutBatch(
            payout_batch_id=payout_batch_id,
            batch_status=self.settle_as,
            items=[PayoutItemStatus(payout_item_id="ITEM-1", transaction_status=self.settle_as)],
        )


@dataclass
class World:
    client: TestClient
    orders: FakeOrders
    payouts: FakePayouts
    failing: set[str]
    as_client: dict[str, str]
    as_freelancer: dict[str, str]

    def create_agreed_milestone(self) -> str:
        body = {"contract": BAKERY, "freelancer_email": "freelancer@pactpay.demo"}
        contract = self.client.post("/contracts", json=body, headers=self.as_client).json()
        self.client.post(f"/contracts/{contract['id']}/approve", headers=self.as_client)
        approved = self.client.post(
            f"/contracts/{contract['id']}/approve", headers=self.as_freelancer
        ).json()
        milestone = approved["milestones"][0]
        assert milestone["state"] == "agreed"
        return milestone["id"]

    def fund(self, milestone_id: str) -> None:
        response = self.client.post(
            f"/milestones/{milestone_id}/fund",
            json={"method": "sandbox_card"},
            headers=self.as_client,
        )
        assert response.status_code == 200, response.text

    def submit_and_wait(self, milestone_id: str) -> dict[str, Any]:
        response = self.client.post(
            f"/milestones/{milestone_id}/submissions",
            json={"live_url": "https://rosies-bakery.example/"},
            headers=self.as_freelancer,
        )
        assert response.status_code == 201, response.text
        return self.wait_for_verdict(milestone_id)

    def wait_for_verdict(self, milestone_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            milestone = self.get(milestone_id)
            if milestone["state"] not in {"submitted", "verifying"}:
                return milestone
            time.sleep(0.05)
        raise AssertionError("Verification did not finish")

    def get(self, milestone_id: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        return self.client.get(
            f"/milestones/{milestone_id}", headers=headers or self.as_client
        ).json()

    def count_payment_events(self, kind: str) -> int:
        async def count() -> int:
            async with self.client.app.state.session_factory() as session:
                return (
                    await session.scalar(
                        select(func.count())
                        .select_from(PaymentEvent)
                        .where(PaymentEvent.kind == kind)
                    )
                    or 0
                )

        return self.client.portal.call(count)


@pytest.fixture
def world(client: TestClient) -> Iterator[World]:
    app = client.app
    orders, payouts, failing = FakeOrders(), FakePayouts(), set()
    settings = app.state.payments.settings.model_copy(update={"payout_poll_attempts": 0})
    runtime = PaymentRuntime(app.state.session_factory, orders, payouts, settings)
    app.state.payments = runtime

    async def check_factory(artifacts: Path, mockup: bytes | None) -> CheckFn:
        async def run(criterion: Criterion, submission: Submission) -> CheckResult:
            passed = criterion.id not in failing
            return CheckResult(
                criterion_id=criterion.id,
                check_type=criterion.check_type,
                passed=passed,
                summary="ok" if passed else "Submitting caused 1 JS error and no confirmation",
            )

        return run

    app.state.verifier = VerificationManager(
        app.state.session_factory, settings, check_factory=check_factory, judge=RuleJudge()
    )
    yield World(
        client,
        orders,
        payouts,
        failing,
        auth_headers(client, "client"),
        auth_headers(client, "freelancer"),
    )


# ------------------------------------------------------------------- happy path


def test_full_flow_fail_fix_pass_paid(world: World) -> None:
    c, mid = world.client, world.create_agreed_milestone()

    # Freelancer can't fund; client funds through PayPal checkout.
    assert (
        c.post(f"/milestones/{mid}/fund", json={}, headers=world.as_freelancer).status_code == 403
    )
    funded = c.post(f"/milestones/{mid}/fund", json={}, headers=world.as_client).json()
    assert funded["approve_url"].endswith("token=ORDER-1")

    # Buyer returns from PayPal (twice: double redirect must be harmless).
    for _ in range(2):
        back = c.get("/paypal/return?token=ORDER-1", follow_redirects=False)
        assert back.status_code == 307
        assert back.headers["location"].endswith(f"/milestones/{mid}?funded=1")
    assert world.orders.calls["authorize"] == 1
    assert world.get(mid)["state"] == "funded"

    # First delivery has a broken form -> FAILED with a fix suggestion.
    world.failing.add("M1-C3")
    failed = world.submit_and_wait(mid)
    assert failed["state"] == "failed"
    report = failed["latest_run"]["report"]
    assert report["overall"] == "failed"
    assert any(c["criterion_id"] == "M1-C3" and c["fix_suggestion"] for c in report["criteria"])

    # No money moves while failed.
    assert c.post(f"/milestones/{mid}/approve-release", headers=world.as_client).status_code == 409
    assert world.orders.calls["capture"] == 0

    # Fix and resubmit -> PASSED.
    world.failing.clear()
    passed = world.submit_and_wait(mid)
    assert passed["state"] == "passed"
    assert len(passed["submissions"]) == 2
    assert "approve_release" in passed["allowed_actions"]
    assert "approve_release" not in world.get(mid, world.as_freelancer)["allowed_actions"]

    # Freelancer can't release their own payment.
    assert (
        c.post(f"/milestones/{mid}/approve-release", headers=world.as_freelancer).status_code == 403
    )

    # Client approves: capture, then payout of amount minus 5% fee.
    released = c.post(f"/milestones/{mid}/approve-release", headers=world.as_client).json()
    assert released["state"] == "released"
    assert released["paypal_capture_id"] == "CAP-1"
    assert released["payout_status"] == "SUCCESS"
    assert world.payouts.sent == [
        {
            "receiver_email": "freelancer-sandbox@personal.example.com",
            "amount": Decimal("142.50"),
            "currency": "USD",
            "sender_batch_id": f"payout-{mid}",
        }
    ]

    # Double-click: nothing moves twice.
    again = c.post(f"/milestones/{mid}/approve-release", headers=world.as_client)
    assert again.json()["state"] == "released"
    assert world.orders.calls["capture"] == 1
    assert len(world.payouts.sent) == 1
    assert world.orders.request_ids == [f"capture-{mid}"]


def test_capture_ok_payout_fails_then_retry_never_double_captures(world: World) -> None:
    mid = world.create_agreed_milestone()
    world.fund(mid)
    world.submit_and_wait(mid)
    world.payouts.fail_next = 1  # the PAYOUT_NOT_AVAILABLE error we hit on Day 1

    response = world.client.post(f"/milestones/{mid}/approve-release", headers=world.as_client)

    assert response.status_code == 502
    stuck = world.get(mid)
    assert stuck["state"] == "releasing"  # captured, not yet paid out
    assert stuck["paypal_capture_id"] == "CAP-1"
    assert "retry_release" in stuck["allowed_actions"]

    retried = world.client.post(f"/milestones/{mid}/retry-release", headers=world.as_client).json()

    assert retried["state"] == "released"
    assert world.orders.calls["capture"] == 1  # the retry skipped the capture
    assert len(world.payouts.sent) == 1
    assert world.count_payment_events("captured") == 1


def test_pending_payout_stays_releasing_until_webhook(world: World) -> None:
    mid = world.create_agreed_milestone()
    world.fund(mid)
    world.submit_and_wait(mid)
    world.payouts.settle_as = "PENDING"

    pending = world.client.post(
        f"/milestones/{mid}/approve-release", headers=world.as_client
    ).json()
    assert pending["state"] == "releasing"

    processor: WebhookProcessor = world.client.app.state.webhook_processor
    world.client.app.state.webhook_processor = WebhookProcessor(
        processor._verifier,
        InMemoryEventStore(),
        world.client.app.state.payments.webhook_handlers(),
        skip_verification=True,
    )
    event = {
        "id": "WH-PAYOUT-1",
        "event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
        "resource": {"payout_batch_id": "BATCH-1", "transaction_status": "SUCCESS"},
    }
    assert world.client.post("/webhooks/paypal", json=event).json() == {"outcome": "processed"}
    assert world.get(mid)["state"] == "released"


# ----------------------------------------------------------------- other paths


def test_cancel_funded_milestone_voids_authorization(world: World) -> None:
    mid = world.create_agreed_milestone()
    world.fund(mid)

    cancelled = world.client.post(f"/milestones/{mid}/cancel", headers=world.as_client).json()

    assert cancelled["state"] == "refunded"
    assert world.orders.calls["void"] == 1
    assert world.orders.calls["capture"] == 0


def test_cancel_before_funding_moves_no_money(world: World) -> None:
    mid = world.create_agreed_milestone()
    cancelled = world.client.post(f"/milestones/{mid}/cancel", headers=world.as_client).json()
    assert cancelled["state"] == "cancelled"
    assert world.orders.calls["void"] == 0


def test_needs_review_then_human_passes(world: World) -> None:
    mid = world.create_agreed_milestone()
    world.fund(mid)

    async def unsure(criterion: Criterion, submission: Submission) -> CheckResult:
        return CheckResult(
            criterion_id=criterion.id, check_type=criterion.check_type, passed=None, summary="?"
        )

    async def factory(artifacts: Path, mockup: bytes | None) -> CheckFn:
        return unsure

    world.client.app.state.verifier._check_factory = factory
    assert world.submit_and_wait(mid)["state"] == "needs_review"

    freelancer_try = world.client.post(
        f"/milestones/{mid}/review", json={"decision": "pass"}, headers=world.as_freelancer
    )
    assert freelancer_try.status_code == 409  # freelancers can't approve their own work
    reviewed = world.client.post(
        f"/milestones/{mid}/review", json={"decision": "pass"}, headers=world.as_client
    ).json()
    assert reviewed["state"] == "passed"


def test_auto_approve_releases_after_timeout(world: World) -> None:
    mid = world.create_agreed_milestone()
    world.fund(mid)
    world.submit_and_wait(mid)

    async def age_and_sweep() -> list[str]:
        async with world.client.app.state.session_factory() as session:
            await session.execute(
                update(MilestoneRecord)
                .where(MilestoneRecord.id == mid)
                .values(passed_at=utcnow() - timedelta(hours=49))
            )
            await session.commit()
        return await world.client.app.state.payments.auto_approve_due()

    assert world.client.portal.call(age_and_sweep) == [mid]
    assert world.get(mid)["state"] == "released"


def test_sse_stream_replays_verification_events(world: World) -> None:
    mid = world.create_agreed_milestone()
    world.fund(mid)
    world.submit_and_wait(mid)

    with world.client.stream(
        "GET", f"/milestones/{mid}/verification/stream", headers=world.as_client
    ) as response:
        events = [
            json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")
        ]

    types = [e["type"] for e in events]
    assert types[0] == "started" and types[-1] == "report_ready"
    assert types.count("check_finished") == 6


def test_webhook_reconciles_capture_we_never_saved(world: World) -> None:
    """Crash story: PayPal captured, we died before saving the capture id."""
    mid = world.create_agreed_milestone()
    world.fund(mid)
    world.client.app.state.webhook_processor = WebhookProcessor(
        world.client.app.state.webhook_processor._verifier,
        InMemoryEventStore(),
        world.client.app.state.payments.webhook_handlers(),
        skip_verification=True,
    )
    event = {
        "id": "WH-CAP-1",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": "CAP-FROM-PAYPAL",
            "status": "COMPLETED",
            "supplementary_data": {"related_ids": {"authorization_id": "AUTH-CARD"}},
        },
    }

    world.client.post("/webhooks/paypal", json=event)

    assert world.get(mid)["paypal_capture_id"] == "CAP-FROM-PAYPAL"


# ----------------------------------------------------------------- auth / access


def test_requires_login(client: TestClient) -> None:
    assert client.get("/contracts").status_code == 401
    bad = client.get("/contracts", headers={"Authorization": "Bearer nope.1.abc"})
    assert bad.status_code == 401


def test_admin_sees_all_but_strangers_get_404(world: World) -> None:
    mid = world.create_agreed_milestone()
    admin = auth_headers(world.client, "admin")
    assert world.client.get(f"/milestones/{mid}", headers=admin).status_code == 200

    async def make_stranger() -> None:
        from app.models.db import User, UserRole

        async with world.client.app.state.session_factory() as session:
            session.add(User(id="stranger", email="s@x.test", name="S", role=UserRole.CLIENT))
            await session.commit()

    world.client.portal.call(make_stranger)
    from app.core.security import issue_token

    token = issue_token(
        "stranger", world.client.app.state.payments.settings.secret_key.get_secret_value()
    )
    response = world.client.get(f"/milestones/{mid}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404


def test_contract_total_must_match_milestones(world: World) -> None:
    bad = {**BAKERY, "total": "999.00"}
    response = world.client.post("/contracts", json={"contract": bad}, headers=world.as_client)
    assert response.status_code == 422
