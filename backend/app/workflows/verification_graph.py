"""Verification workflow for one milestone submission.

    START -> plan -> run_checks (parallel) -> judge (parallel, one call per criterion) -> report

`run_checks` and `judge` fan out with bounded concurrency. Progress events are emitted for
the live UI. The workflow only produces a report; it never moves money.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.contract_schema import CheckType, Criterion, Milestone
from app.agents.judge import Judge, combine, manual_verdict
from app.verification.models import CheckResult
from app.verification.report import (
    CriterionVerdict,
    Submission,
    VerificationEvent,
    VerificationReport,
    overall_verdict,
)

logger = logging.getLogger(__name__)

CheckFn = Callable[[Criterion, Submission], Awaitable[CheckResult]]
EventFn = Callable[[VerificationEvent], Awaitable[None]]


class VerificationState(TypedDict, total=False):
    milestone: Milestone
    submission: Submission
    started_at: datetime
    automated: list[Criterion]
    manual: list[Criterion]
    checks: dict[str, CheckResult]
    verdicts: list[CriterionVerdict]
    report: VerificationReport


async def _ignore(_: VerificationEvent) -> None:
    return None


class VerificationWorkflow:
    def __init__(
        self,
        run_check: CheckFn,
        judge: Judge,
        *,
        confidence_threshold: float = 0.8,
        max_parallel_checks: int = 3,
        on_event: EventFn = _ignore,
    ) -> None:
        self._run_check = run_check
        self._judge = judge
        self._threshold = confidence_threshold
        self._semaphore = asyncio.Semaphore(max_parallel_checks)
        self._emit = on_event

        graph = StateGraph(VerificationState)
        graph.add_node("plan", self._plan)
        graph.add_node("run_checks", self._run_checks)
        graph.add_node("judge", self._judge_all)
        graph.add_node("report", self._report)
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "run_checks")
        graph.add_edge("run_checks", "judge")
        graph.add_edge("judge", "report")
        graph.add_edge("report", END)
        self._graph = graph.compile()

    async def run(self, milestone: Milestone, submission: Submission) -> VerificationReport:
        state = await self._graph.ainvoke(
            {"milestone": milestone, "submission": submission, "started_at": datetime.now(UTC)}
        )
        return state["report"]

    async def _plan(self, state: VerificationState) -> VerificationState:
        criteria = state["milestone"].criteria
        await self._emit(VerificationEvent(type="started", summary=f"{len(criteria)} criteria"))
        return {
            "automated": [
                c for c in criteria if c.is_objective and c.check_type != CheckType.MANUAL
            ],
            "manual": [
                c for c in criteria if not c.is_objective or c.check_type == CheckType.MANUAL
            ],
        }

    async def _run_checks(self, state: VerificationState) -> VerificationState:
        submission = state["submission"]

        async def run_one(criterion: Criterion) -> CheckResult:
            async with self._semaphore:
                await self._emit(
                    VerificationEvent(
                        type="check_started",
                        criterion_id=criterion.id,
                        check_type=criterion.check_type,
                        summary=criterion.description,
                    )
                )
                try:
                    result = await self._run_check(criterion, submission)
                except Exception as exc:  # a crash in one check must not sink the run
                    logger.exception("Check %s crashed", criterion.id)
                    result = CheckResult(
                        criterion_id=criterion.id,
                        check_type=criterion.check_type,
                        passed=None,
                        summary="Check crashed",
                        error=str(exc)[:300],
                    )
                await self._emit(
                    VerificationEvent(
                        type="check_finished",
                        criterion_id=criterion.id,
                        check_type=criterion.check_type,
                        status={True: "passed", False: "failed", None: "unknown"}[result.passed],
                        summary=result.summary,
                        screenshot=next(iter(result.evidence.screenshots[::-1]), None),
                    )
                )
                return result

        results = await asyncio.gather(*(run_one(c) for c in state["automated"]))
        return {"checks": {r.criterion_id: r for r in results}}

    async def _judge_all(self, state: VerificationState) -> VerificationState:
        async def judge_one(criterion: Criterion) -> CriterionVerdict:
            check = state["checks"][criterion.id]
            decision = (
                await self._judge.judge(criterion, check) if check.passed is not None else None
            )
            verdict = combine(
                criterion,
                check,
                decision,
                threshold=self._threshold,
                decided_by=self._judge.name,
            )
            await self._emit(
                VerificationEvent(
                    type="judged",
                    criterion_id=criterion.id,
                    check_type=criterion.check_type,
                    status=verdict.verdict,
                    summary=verdict.reasoning,
                )
            )
            return verdict

        judged = await asyncio.gather(*(judge_one(c) for c in state["automated"]))
        manual = [manual_verdict(c) for c in state["manual"]]
        order = {c.id: i for i, c in enumerate(state["milestone"].criteria)}
        verdicts = sorted([*judged, *manual], key=lambda v: order[v.criterion_id])
        return {"verdicts": verdicts}

    async def _report(self, state: VerificationState) -> VerificationState:
        verdicts = state["verdicts"]
        finished = datetime.now(UTC)
        counts: dict[str, int] = {}
        for v in verdicts:
            counts[v.verdict] = counts.get(v.verdict, 0) + 1
        report = VerificationReport(
            milestone_id=state["milestone"].id,
            submission=state["submission"],
            overall=overall_verdict(verdicts),
            criteria=verdicts,
            counts=counts,
            started_at=state["started_at"],
            finished_at=finished,
            duration_ms=int((finished - state["started_at"]).total_seconds() * 1000),
        )
        await self._emit(VerificationEvent(type="report_ready", status=report.overall))
        return {"report": report}
