import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agents.judge import Judge, LLMJudge, RuleJudge
from app.api.auth import router as auth_router
from app.api.contracts import router as contracts_router
from app.api.health import router as health_router
from app.api.milestones import router as milestones_router
from app.api.webhooks import router as webhooks_router
from app.core.config import DEV_SECRET_KEY, Settings, get_settings
from app.core.db import create_engine, create_session_factory
from app.core.llm import StructuredLLM, build_llm
from app.core.logging import configure_logging
from app.models.db import Base
from app.paypal.client import PayPalClient
from app.paypal.errors import PayPalError
from app.paypal.orders import OrdersService
from app.paypal.payouts import PayoutsService
from app.paypal.webhooks import WebhookProcessor, WebhookVerifier
from app.security.scanner import SubmissionScanner
from app.security.url_guard import URLGuard
from app.services.contracts import PermissionDeniedError
from app.services.demo import ensure_demo_users
from app.services.milestones import ConcurrentUpdateError, NotFoundError
from app.services.payments import PaymentError, PaymentRuntime
from app.services.records import SqlEventStore
from app.services.verification_manager import RunTools, VerificationManager
from app.verification.runner import CheckRunner
from app.verification.web_checks import LLMSelectorSuggester, WebChecker
from app.workflows.milestone_state import IllegalTransitionError

logger = logging.getLogger(__name__)


class BrowserHolder:
    """One Chromium per process, started on first use (most requests never need it)."""

    def __init__(self, settings: Settings, llm: StructuredLLM | None, guard: URLGuard) -> None:
        self._settings = settings
        self._llm = llm
        self._guard = guard
        self._web: WebChecker | None = None
        self._lock = asyncio.Lock()

    async def check_factory(self, artifacts: Path, mockup: bytes | None) -> RunTools:
        async with self._lock:
            if self._web is None:
                self._web = await WebChecker(
                    self._settings.artifacts_dir,
                    timeout_seconds=self._settings.check_timeout_seconds,
                    locator=LLMSelectorSuggester(self._llm) if self._llm else None,
                    url_guard=self._guard,
                ).__aenter__()
        web = self._web.scoped(artifacts)
        return RunTools(
            run_check=CheckRunner(
                web,
                artifacts_dir=artifacts,
                llm=self._llm,
                mockup=mockup,
                repo_max_size_mb=self._settings.repo_max_size_mb,
                repo_timeout_seconds=self._settings.repo_clone_timeout_seconds,
            ),
            security_scan=SubmissionScanner(
                web,
                repo_max_size_mb=self._settings.repo_max_size_mb,
                repo_timeout_seconds=self._settings.repo_clone_timeout_seconds,
            ),
        )

    async def aclose(self) -> None:
        if self._web:
            await self._web.__aexit__(None, None, None)


async def _auto_approve_loop(payments: PaymentRuntime, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            if released := await payments.auto_approve_due():
                logger.info("Auto-approved %s milestone(s): %s", len(released), released)
        except Exception:
            logger.exception("Auto-approve sweep failed")


def _check_production_settings(settings: Settings) -> None:
    if settings.app_env != "production":
        return
    if settings.paypal_webhook_skip_verify:
        raise RuntimeError("PAYPAL_WEBHOOK_SKIP_VERIFY must never be enabled in production")
    if settings.secret_key.get_secret_value() == DEV_SECRET_KEY:
        raise RuntimeError("Set SECRET_KEY in production")
    if settings.allow_private_urls:
        raise RuntimeError("ALLOW_PRIVATE_URLS must never be enabled in production (SSRF)")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    _check_production_settings(settings)
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)

    engine = create_engine(settings.database_url)
    if settings.database_auto_create:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    app.state.session_factory = session_factory
    if settings.demo_mode:
        async with session_factory() as session:
            await ensure_demo_users(session, settings)

    paypal = PayPalClient.from_settings(settings)
    app.state.paypal = paypal
    payments = PaymentRuntime(
        session_factory, OrdersService(paypal), PayoutsService(paypal), settings
    )
    app.state.payments = payments
    app.state.webhook_processor = WebhookProcessor(
        WebhookVerifier(paypal, settings.paypal_webhook_id),
        SqlEventStore(session_factory),
        payments.webhook_handlers(),
        skip_verification=settings.paypal_webhook_skip_verify,
    )

    llm = build_llm(settings)
    app.state.llm = llm
    judge: Judge = LLMJudge(llm) if llm else RuleJudge()
    guard = URLGuard(allow_private=settings.allow_private_urls)
    app.state.url_guard = guard
    browser = BrowserHolder(settings, llm, guard)
    app.state.verifier = VerificationManager(
        session_factory, settings, check_factory=browser.check_factory, judge=judge
    )
    if recovered := await app.state.verifier.recover():
        logger.warning("Recovered %s interrupted verification run(s)", recovered)

    app.state.background_tasks = set()
    scheduler = (
        asyncio.create_task(_auto_approve_loop(payments, settings.auto_approve_check_seconds))
        if settings.auto_approve_check_seconds > 0
        else None
    )
    try:
        yield
    finally:
        if scheduler:
            scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scheduler
        for task in list(app.state.background_tasks):
            task.cancel()
        await browser.aclose()
        await paypal.aclose()
        await engine.dispose()


def _error(status: int, message: str, **extra: object) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": message, **extra})


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

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return _error(404, str(exc))

    @app.exception_handler(IllegalTransitionError)
    async def _illegal(_: Request, exc: IllegalTransitionError) -> JSONResponse:
        return _error(409, str(exc), state=exc.state.value, action=exc.action.value)

    @app.exception_handler(ConcurrentUpdateError)
    async def _conflict(_: Request, exc: ConcurrentUpdateError) -> JSONResponse:
        return _error(409, f"{exc}. Reload and try again.")

    @app.exception_handler(PaymentError)
    async def _payment(_: Request, exc: PaymentError) -> JSONResponse:
        return _error(409, str(exc))

    @app.exception_handler(PayPalError)
    async def _paypal(_: Request, exc: PayPalError) -> JSONResponse:
        logger.error("PayPal error: %s", exc)
        return _error(502, f"PayPal error: {exc.message}", paypal_debug_id=exc.debug_id)

    @app.exception_handler(PermissionError)
    @app.exception_handler(PermissionDeniedError)
    async def _forbidden(_: Request, exc: Exception) -> JSONResponse:
        return _error(403, str(exc))

    @app.exception_handler(ValueError)
    async def _bad_request(_: Request, exc: ValueError) -> JSONResponse:
        return _error(422, str(exc))

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(contracts_router)
    app.include_router(milestones_router)
    app.include_router(webhooks_router)

    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/artifacts", StaticFiles(directory=settings.artifacts_dir), name="artifacts")
    return app


app = create_app()
