"""Runs verification in the background and fans progress out to SSE subscribers.

Verification takes 10-30s, so the API returns immediately with a run id and the browser
follows along on the SSE stream. Each run has an event channel with full history, so a
subscriber that connects late still sees every step.

Runs execute in-process (asyncio tasks, bounded by a semaphore) and share one Chromium.
The database is the source of truth: if the process restarts mid-run, `recover()` marks the
run as errored and returns the milestone to SUBMITTED so it can be re-verified.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.judge import Judge
from app.core.config import Settings
from app.models.db import MilestoneRecord, VerificationRunRecord
from app.services.contracts import to_domain_milestone
from app.services.milestones import MilestoneService
from app.services.records import (
    fail_verification_run,
    save_verification_report,
    start_verification_run,
)
from app.verification.report import (
    OverallVerdict,
    Submission,
    VerificationEvent,
    VerificationReport,
)
from app.workflows.milestone_state import Action, MilestoneState, Role
from app.workflows.verification_graph import CheckFn, ScanFn, VerificationWorkflow

logger = logging.getLogger(__name__)


@dataclass
class RunTools:
    run_check: CheckFn
    security_scan: ScanFn | None = None


# Builds the tools for one run: (artifacts dir for this run, mockup bytes) -> RunTools
CheckFactory = Callable[[Path, bytes | None], Awaitable[RunTools]]

OUTCOME_ACTIONS = {
    OverallVerdict.PASSED: Action.VERIFICATION_PASSED,
    OverallVerdict.FAILED: Action.VERIFICATION_FAILED,
    OverallVerdict.NEEDS_REVIEW: Action.VERIFICATION_UNCERTAIN,
}


class EventChannel:
    def __init__(self) -> None:
        self.history: list[VerificationEvent] = []
        self._subscribers: set[asyncio.Queue[VerificationEvent | None]] = set()
        self.closed = False

    async def publish(self, event: VerificationEvent) -> None:
        self.history.append(event)
        for queue in self._subscribers:
            queue.put_nowait(event)

    def close(self) -> None:
        self.closed = True
        for queue in self._subscribers:
            queue.put_nowait(None)

    async def stream(self) -> AsyncIterator[VerificationEvent]:
        # Snapshot history and subscribe with no await in between, so no event is lost
        # or duplicated (asyncio is single-threaded between awaits).
        backlog = list(self.history)
        if self.closed:
            for event in backlog:
                yield event
            return
        queue: asyncio.Queue[VerificationEvent | None] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            for event in backlog:
                yield event
            while (event := await queue.get()) is not None:
                yield event
        finally:
            self._subscribers.discard(queue)


class VerificationManager:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        check_factory: CheckFactory,
        judge: Judge,
        max_concurrent_runs: int = 2,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._check_factory = check_factory
        self._judge = judge
        self._slots = asyncio.Semaphore(max_concurrent_runs)
        self._channels: dict[str, EventChannel] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def start(self, milestone_id: str, *, actor: str) -> str:
        """Move SUBMITTED -> VERIFYING, create a run, and start it in the background."""
        async with self._session_factory() as session:
            service = MilestoneService(session)
            milestone = await service.get(milestone_id)
            if not milestone.submissions:
                raise ValueError("Nothing has been submitted for this milestone")
            submission = milestone.submissions[-1]
            await service.apply(milestone, Action.START_VERIFICATION, role=Role.SYSTEM, actor=actor)
            run = await start_verification_run(session, milestone.id, submission.id)
            domain = to_domain_milestone(milestone)
            mockup = self._load_mockup(milestone.contract.mockup_path)
            target = Submission(live_url=submission.live_url, repo_url=submission.repo_url)

        channel = EventChannel()
        self._channels[run.id] = channel
        self._tasks[run.id] = asyncio.create_task(
            self._execute(run.id, milestone_id, domain, target, mockup, channel)
        )
        return run.id

    def channel(self, run_id: str) -> EventChannel | None:
        return self._channels.get(run_id)

    async def wait(self, run_id: str) -> None:
        if task := self._tasks.get(run_id):
            await task

    async def recover(self) -> int:
        """After a restart: fail runs that were in flight and allow re-verification."""
        async with self._session_factory() as session:
            stuck = list(
                await session.scalars(
                    select(VerificationRunRecord).where(VerificationRunRecord.status == "running")
                )
            )
            for run in stuck:
                await fail_verification_run(session, run, "Interrupted by a server restart")
                milestone = await MilestoneService(session).get(run.milestone_id)
                if milestone.state is MilestoneState.VERIFYING:
                    await MilestoneService(session).apply(
                        milestone, Action.VERIFICATION_ERROR, role=Role.SYSTEM, actor="system"
                    )
        return len(stuck)

    async def _execute(
        self,
        run_id: str,
        milestone_id: str,
        domain_milestone: object,
        submission: Submission,
        mockup: bytes | None,
        channel: EventChannel,
    ) -> None:
        artifacts = self._settings.artifacts_dir / "runs" / run_id
        artifacts.mkdir(parents=True, exist_ok=True)
        try:
            async with self._slots:
                tools = await self._check_factory(artifacts, mockup)
                workflow = VerificationWorkflow(
                    tools.run_check,
                    self._judge,
                    confidence_threshold=self._settings.judge_confidence_threshold,
                    on_event=lambda e: channel.publish(_public_event(e, self._settings)),
                    security_scan=tools.security_scan,
                )
                report = await workflow.run(domain_milestone, submission)  # type: ignore[arg-type]
            report = _public_report(report, self._settings)
            await self._finish(run_id, milestone_id, report)
        except Exception as exc:
            logger.exception("Verification run %s failed", run_id)
            await self._fail(run_id, milestone_id, str(exc))
            await channel.publish(
                VerificationEvent(type="report_ready", status="error", summary=str(exc)[:200])
            )
        finally:
            channel.close()

    async def _finish(self, run_id: str, milestone_id: str, report: VerificationReport) -> None:
        async with self._session_factory() as session:
            run = await session.get(VerificationRunRecord, run_id)
            assert run is not None
            await save_verification_report(session, run, report)
            service = MilestoneService(session)
            milestone = await service.get(milestone_id)
            await service.apply(
                milestone,
                OUTCOME_ACTIONS[report.overall],
                role=Role.SYSTEM,
                actor="verifier",
                details={"run_id": run_id, "counts": report.counts},
            )

    async def _fail(self, run_id: str, milestone_id: str, error: str) -> None:
        async with self._session_factory() as session:
            run = await session.get(VerificationRunRecord, run_id)
            if run:
                await fail_verification_run(session, run, error)
            service = MilestoneService(session)
            milestone = await service.get(milestone_id)
            if milestone.state is MilestoneState.VERIFYING:
                await service.apply(
                    milestone, Action.VERIFICATION_ERROR, role=Role.SYSTEM, actor="verifier"
                )

    def _load_mockup(self, mockup_path: str | None) -> bytes | None:
        if not mockup_path:
            return None
        path = Path(mockup_path)
        try:
            return path.read_bytes()
        except OSError:
            logger.warning("Mockup %s not readable", path)
            return None


async def latest_run(
    session: AsyncSession, milestone: MilestoneRecord
) -> VerificationRunRecord | None:
    return await session.scalar(
        select(VerificationRunRecord)
        .where(VerificationRunRecord.milestone_id == milestone.id)
        .order_by(VerificationRunRecord.started_at.desc())
        .limit(1)
    )


def artifact_url(path: str | None, settings: Settings) -> str | None:
    """Turn a local artifact path into the URL it's served at (/artifacts/...)."""
    if not path:
        return None
    try:
        relative = Path(path).resolve().relative_to(settings.artifacts_dir.resolve())
    except ValueError:
        return None  # never expose files outside the artifacts dir
    return f"/artifacts/{relative.as_posix()}"


def _public_event(event: VerificationEvent, settings: Settings) -> VerificationEvent:
    if event.screenshot:
        return event.model_copy(update={"screenshot": artifact_url(event.screenshot, settings)})
    return event


def _public_report(report: VerificationReport, settings: Settings) -> VerificationReport:
    for criterion in report.criteria:
        if criterion.check:
            evidence = criterion.check.evidence
            evidence.screenshots = [
                url for p in evidence.screenshots if (url := artifact_url(p, settings))
            ]
    return report
