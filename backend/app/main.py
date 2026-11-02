from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.contracts import router as contracts_router
from app.api.health import router as health_router
from app.api.webhooks import router as webhooks_router
from app.core.config import get_settings
from app.core.llm import build_llm
from app.core.logging import configure_logging
from app.paypal.client import PayPalClient
from app.paypal.webhooks import (
    DEFAULT_HANDLERS,
    InMemoryEventStore,
    WebhookProcessor,
    WebhookVerifier,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.paypal_webhook_skip_verify and settings.app_env == "production":
        raise RuntimeError("PAYPAL_WEBHOOK_SKIP_VERIFY must never be enabled in production")

    paypal = PayPalClient.from_settings(settings)
    app.state.paypal = paypal
    app.state.llm = build_llm(settings)
    app.state.webhook_processor = WebhookProcessor(
        WebhookVerifier(paypal, settings.paypal_webhook_id),
        InMemoryEventStore(),
        DEFAULT_HANDLERS,
        skip_verification=settings.paypal_webhook_skip_verify,
    )
    try:
        yield
    finally:
        await paypal.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="PactPay API",
        description="AI-verified escrow for freelance work, powered by PayPal.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(webhooks_router)
    app.include_router(contracts_router)
    return app


app = create_app()
