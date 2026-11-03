import asyncio
from pathlib import Path

import pytest

from app.agents.contract_schema import CheckType, Contract, Criterion
from app.agents.judge import RuleJudge
from app.verification.models import CheckResult
from app.verification.report import OverallVerdict, Submission, Verdict, VerificationEvent
from app.verification.runner import CheckRunner
from app.verification.web_checks import WebChecker
from app.workflows.verification_graph import VerificationWorkflow
from tests.conftest import DEMO_DIR

pytestmark = pytest.mark.asyncio(loop_scope="session")

BAKERY = Contract.model_validate_json((DEMO_DIR / "contracts" / "bakery.json").read_text())
MILESTONE = BAKERY.milestones[0]
SUBMISSION = Submission(live_url="https://freelancer.example/")


def fake_checks(results: dict[str, bool | None]):  # noqa: ANN201
    async def run(criterion: Criterion, submission: Submission) -> CheckResult:
        await asyncio.sleep(0)
        return CheckResult(
            criterion_id=criterion.id,
            check_type=criterion.check_type,
            passed=results.get(criterion.id, True),
            summary=f"{criterion.id} result",
        )

    return run


async def test_all_checks_pass_gives_passed_with_manual_items_pending() -> None:
    report = await VerificationWorkflow(fake_checks({}), RuleJudge()).run(MILESTONE, SUBMISSION)

    assert report.overall is OverallVerdict.PASSED
    assert report.counts == {"pass": 6, "manual": 1}
    assert [c.criterion_id for c in report.criteria] == [c.id for c in MILESTONE.criteria]
    assert report.criteria[-1].verdict is Verdict.MANUAL


async def test_one_failing_check_fails_the_milestone() -> None:
    report = await VerificationWorkflow(fake_checks({"M1-C3": False}), RuleJudge()).run(
        MILESTONE, SUBMISSION
    )

    assert report.overall is OverallVerdict.FAILED
    failed = next(c for c in report.criteria if c.criterion_id == "M1-C3")
    assert failed.verdict is Verdict.FAIL
    assert report.fixes  # the freelancer is told what to fix


async def test_check_that_cannot_run_routes_to_human() -> None:
    report = await VerificationWorkflow(fake_checks({"M1-C6": None}), RuleJudge()).run(
        MILESTONE, SUBMISSION
    )

    assert report.overall is OverallVerdict.NEEDS_REVIEW


async def test_crashing_check_does_not_sink_the_run() -> None:
    async def crash(criterion: Criterion, submission: Submission) -> CheckResult:
        if criterion.id == "M1-C2":
            raise RuntimeError("boom")
        return await fake_checks({})(criterion, submission)

    report = await VerificationWorkflow(crash, RuleJudge()).run(MILESTONE, SUBMISSION)

    crashed = next(c for c in report.criteria if c.criterion_id == "M1-C2")
    assert crashed.verdict is Verdict.NEEDS_REVIEW
    assert report.overall is OverallVerdict.NEEDS_REVIEW


async def test_events_are_emitted_in_order() -> None:
    events: list[VerificationEvent] = []

    async def record(event: VerificationEvent) -> None:
        events.append(event)

    await VerificationWorkflow(fake_checks({}), RuleJudge(), on_event=record).run(
        MILESTONE, SUBMISSION
    )

    types = [e.type for e in events]
    assert types[0] == "started" and types[-1] == "report_ready"
    assert types.count("check_started") == types.count("check_finished") == 6
    assert types.count("judged") == 6


async def test_parallelism_is_bounded() -> None:
    running = peak = 0

    async def slow(criterion: Criterion, submission: Submission) -> CheckResult:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return await fake_checks({})(criterion, submission)

    await VerificationWorkflow(slow, RuleJudge(), max_parallel_checks=2).run(MILESTONE, SUBMISSION)

    assert peak == 2


async def test_only_manual_criteria_needs_review() -> None:
    manual_only = MILESTONE.model_copy(
        update={"criteria": [c for c in MILESTONE.criteria if c.check_type is CheckType.MANUAL]}
    )
    report = await VerificationWorkflow(fake_checks({}), RuleJudge()).run(manual_only, SUBMISSION)
    assert report.overall is OverallVerdict.NEEDS_REVIEW


# ------------------------------------------------- end to end on real demo sites


@pytest.mark.parametrize(
    ("site", "overall", "failed"),
    [
        ("good-site", OverallVerdict.NEEDS_REVIEW, set()),  # visual_match needs an LLM here
        ("buggy-site", OverallVerdict.FAILED, {"M1-C3", "M1-C4"}),
        ("malicious-site", OverallVerdict.FAILED, {"M1-C3", "M1-C4"}),
    ],
)
async def test_demo_sites_end_to_end(
    web: WebChecker,
    demo_url: str,
    tmp_path: Path,
    site: str,
    overall: OverallVerdict,
    failed: set[str],
) -> None:
    runner = CheckRunner(web, artifacts_dir=tmp_path)  # no LLM: rules judge, no visual match
    report = await VerificationWorkflow(runner, RuleJudge()).run(
        MILESTONE, Submission(live_url=f"{demo_url}/{site}/")
    )

    assert report.overall is overall
    assert {c.criterion_id for c in report.criteria if c.verdict is Verdict.FAIL} == failed
    assert not any(c.verdict is Verdict.PASS and c.criterion_id in failed for c in report.criteria)
