"""Dispatch a criterion to the check that verifies it."""

import time
import uuid
from pathlib import Path

from app.agents.contract_schema import CheckType, Criterion
from app.core.llm import StructuredLLM
from app.verification.models import CheckResult
from app.verification.repo_check import repo_check
from app.verification.report import Submission
from app.verification.visual_match import visual_match
from app.verification.web_checks import WebChecker, resolve_url


class CheckRunner:
    def __init__(
        self,
        web: WebChecker,
        *,
        artifacts_dir: Path,
        llm: StructuredLLM | None = None,
        mockup: bytes | None = None,
        repo_max_size_mb: int = 50,
        repo_timeout_seconds: float = 60,
    ) -> None:
        self._web = web
        self._artifacts_dir = artifacts_dir
        self._llm = llm
        self._mockup = mockup
        self._repo_max_size_mb = repo_max_size_mb
        self._repo_timeout_seconds = repo_timeout_seconds

    async def __call__(self, criterion: Criterion, submission: Submission) -> CheckResult:
        p = criterion.parameters
        try:
            url = resolve_url(submission.live_url, p.path)
        except ValueError as exc:
            return _no_verdict(criterion, str(exc))

        match criterion.check_type:
            case CheckType.PAGE_LOADS:
                return await self._web.page_loads(criterion.id, url)
            case CheckType.ELEMENT_PRESENT:
                return await self._web.element_present(
                    criterion.id,
                    url,
                    selector=p.selector,
                    text=p.text,
                    description=criterion.description,
                )
            case CheckType.FORM_SUBMITS:
                return await self._web.form_submits(
                    criterion.id, url, success_text=p.success_text, form_selector=p.selector
                )
            case CheckType.RESPONSIVE_LAYOUT:
                return await self._web.responsive_layout(
                    criterion.id, url, viewports=p.viewports or [375, 1440]
                )
            case CheckType.PERFORMANCE:
                return await self._web.performance(
                    criterion.id,
                    url,
                    max_load_seconds=p.max_load_seconds or 3.0,
                    max_page_weight_kb=p.max_page_weight_kb or 2000,
                )
            case CheckType.VISUAL_MATCH:
                return await self._visual_match(criterion, url)
            case CheckType.REPO_CHECK:
                if not submission.repo_url:
                    return _failed(criterion, "No repository URL was submitted")
                return await repo_check(
                    criterion.id,
                    submission.repo_url,
                    required_files=p.required_files,
                    max_size_mb=self._repo_max_size_mb,
                    timeout_seconds=self._repo_timeout_seconds,
                )
            case CheckType.TESTS_PASS:
                return _no_verdict(criterion, "Running repository tests is not supported yet")
            case _:
                return _no_verdict(criterion, f"No automated check for {criterion.check_type}")

    async def _visual_match(self, criterion: Criterion, url: str) -> CheckResult:
        if self._llm is None or self._mockup is None:
            return _no_verdict(criterion, "Visual match needs a mockup and a vision model")
        started = time.monotonic()
        screenshot = await self._web.screenshot(url)
        path = self._artifacts_dir / f"{criterion.id}_live_{uuid.uuid4().hex[:6]}.png"
        path.write_bytes(screenshot)
        result = await visual_match(
            self._llm,
            criterion_id=criterion.id,
            mockup=self._mockup,
            screenshot=screenshot,
            min_match_score=criterion.parameters.min_match_score or 0.7,
            screenshot_path=path,
        )
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result


def _no_verdict(criterion: Criterion, reason: str) -> CheckResult:
    return CheckResult(
        criterion_id=criterion.id, check_type=criterion.check_type, passed=None, summary=reason
    )


def _failed(criterion: Criterion, reason: str) -> CheckResult:
    return CheckResult(
        criterion_id=criterion.id, check_type=criterion.check_type, passed=False, summary=reason
    )
