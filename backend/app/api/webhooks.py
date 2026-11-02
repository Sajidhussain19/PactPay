from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.paypal.webhooks import InvalidWebhookError, WebhookOutcome, WebhookProcessor

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookAck(BaseModel):
    outcome: WebhookOutcome


@router.post("/paypal", response_model=WebhookAck)
async def paypal_webhook(request: Request) -> WebhookAck:
    processor: WebhookProcessor = request.app.state.webhook_processor
    raw_body = await request.body()
    try:
        outcome = await processor.process(request.headers, raw_body)
    except InvalidWebhookError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 200 for duplicates/ignored too, otherwise PayPal keeps retrying.
    return WebhookAck(outcome=outcome)
