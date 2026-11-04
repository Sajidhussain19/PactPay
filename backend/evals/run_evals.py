"""Measure how trustworthy the verifier is on labeled test deliverables.

    cd backend
    python -m evals.run_evals --judge rules          # free, deterministic (runs in CI)
    python -m evals.run_evals --judge llm            # real gpt-4o judge + vision (headline)
    python -m evals.run_evals --judge rules --max-false-pass 0.05   # exit 1 above 5%

Writes evals/report-<judge>.md and evals/out/results-<judge>.json.
"""

import argparse
import asyncio
import json
import shutil
import sys
import threading
import time
from dataclasses import asdict
from datetime import UTC, datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from app.agents.contract_schema import Contract
from app.agents.judge import LLMJudge, RuleJudge
from app.core.config import get_settings
from app.core.llm import OpenAIStructuredLLM, build_llm
from app.core.logging import configure_logging
from app.security.scanner import SubmissionScanner
from app.verification.report import Submission, VerificationReport
from app.verification.runner import CheckRunner
from app.verification.web_checks import WebChecker
from app.workflows.verification_graph import VerificationWorkflow
from evals.cases import CASES, CRITERIA, Case
from evals.metrics import CaseOutcome, CriterionOutcome, EvalMetrics, summarize

EVALS_DIR = Path(__file__).resolve().parent
OUT_DIR = EVALS_DIR / "out"
DEMO_DIR = EVALS_DIR.parents[1] / "demo"
BAKERY = DEMO_DIR / "contracts" / "bakery.json"
MOCKUP = DEMO_DIR / "mockup" / "mockup.png"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass


def serve(directory: Path) -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=directory))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


async def run_case(
    case: Case, base_url: str, web: WebChecker, workflow_for: Any
) -> tuple[CaseOutcome, list[CriterionOutcome], VerificationReport | None]:
    artifacts = OUT_DIR / "artifacts" / case.id
    workflow, milestone = workflow_for(web.scoped(artifacts), artifacts)
    started = time.monotonic()
    try:
        report = await workflow.run(milestone, Submission(live_url=f"{base_url}/{case.id}/"))
    except Exception as exc:  # an eval crash is a result too
        print(f"  {case.id}: ERROR {exc}")
        outcome = CaseOutcome(case.id, case.should_auto_pass, "error", case.injection, False, 0)
        return outcome, [], None
    duration = int((time.monotonic() - started) * 1000)
    verdicts = {c.criterion_id: c for c in report.criteria}
    decisions = [
        CriterionOutcome(
            case_id=case.id,
            criterion=criterion,
            label=case.label(criterion),
            verdict=verdicts[criterion].verdict.value,
            summary=(verdicts[criterion].check.summary if verdicts[criterion].check else "")[:160],
        )
        for criterion in CRITERIA
    ]
    outcome = CaseOutcome(
        case_id=case.id,
        should_auto_pass=case.should_auto_pass,
        overall=report.overall.value,
        injection_expected=case.injection,
        flagged=bool(report.security_flags),
        duration_ms=duration,
        tags=case.tags,
    )
    mark = "ok " if (outcome.overall == "passed") == case.should_auto_pass else "!! "
    print(f"  {mark}{case.id:28} {outcome.overall:13} flags={len(report.security_flags)}")
    return outcome, decisions, report


async def run(judge_mode: str, case_ids: set[str] | None, concurrency: int) -> dict[str, Any]:
    settings = get_settings()
    llm = build_llm(settings) if judge_mode == "llm" else None
    if judge_mode == "llm" and llm is None:
        sys.exit("--judge llm needs OPENAI_API_KEY")
    contract = Contract.model_validate_json(BAKERY.read_text())
    milestone = contract.milestones[0]
    mockup = MOCKUP.read_bytes()
    judge = LLMJudge(llm) if llm else RuleJudge()

    cases = [c for c in CASES if not case_ids or c.id in case_ids]
    sites = OUT_DIR / "sites"
    for generated in (sites, OUT_DIR / "artifacts"):  # keep other modes' results
        shutil.rmtree(generated, ignore_errors=True)
    for case in cases:
        case.build(sites / case.id)
    base_url = serve(sites)
    print(f"Running {len(cases)} cases with the {judge_mode} judge...")

    def workflow_for(web: WebChecker, artifacts: Path) -> tuple[VerificationWorkflow, Any]:
        workflow = VerificationWorkflow(
            CheckRunner(web, artifacts_dir=artifacts, llm=llm, mockup=mockup),
            judge,
            confidence_threshold=settings.judge_confidence_threshold,
            security_scan=SubmissionScanner(web, scan_repo=False),
        )
        return workflow, milestone

    slots = asyncio.Semaphore(concurrency)
    started = time.monotonic()
    async with WebChecker(OUT_DIR / "artifacts", timeout_seconds=20) as web:

        async def bounded(case: Case) -> Any:
            async with slots:
                return await run_case(case, base_url, web, workflow_for)

        results = await asyncio.gather(*(bounded(c) for c in cases))

    usage = dict(llm.usage) if isinstance(llm, OpenAIStructuredLLM) else {}
    return {
        "judge": judge_mode,
        "model": settings.llm_model if llm else None,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "wall_seconds": round(time.monotonic() - started, 1),
        "usage": usage,
        "cases": [asdict(r[0]) for r in results],
        "decisions": [asdict(d) for r in results for d in r[1]],
        "flags": {
            r[0].case_id: [f.model_dump(mode="json") for f in r[2].security_flags]
            for r in results
            if r[2]
        },
    }


def render_report(raw: dict[str, Any], metrics: EvalMetrics, costs: tuple[float, float]) -> str:
    cases = {c.id: c for c in CASES}
    usage = raw["usage"]
    n = metrics.cases or 1
    cost = None
    if usage:
        cost = (usage["input_tokens"] * costs[0] + usage["output_tokens"] * costs[1]) / 1e6

    lines = [
        f"# PactPay verifier evals ({raw['judge']} judge)",
        "",
        f"Run {raw['started_at']} on {metrics.cases} labeled test deliverables "
        f"({metrics.decisions} scored criterion decisions). Judge: "
        + (
            f"`{raw['model']}` (LLM judge + vision mockup match)"
            if raw["model"]
            else "rules only (no LLM; visual match is routed to a human)"
        )
        + ".",
        "",
        "## What happens to the money",
        "",
        "| Metric | Result | Meaning |",
        "|---|---|---|",
        f"| **Unsafe auto-pass** | **{metrics.unsafe_auto_pass}** | Defective or manipulated "
        "deliveries that would have been paid without a human looking |",
        f"| Honest deliveries auto-passed | {metrics.good_auto_passed} | Complete work paid "
        "without friction |",
        f"| Honest deliveries wrongly failed | {metrics.good_wrongly_failed} | Unfair to the "
        "freelancer (they can resubmit or dispute) |",
        "",
        "## Criterion-level accuracy",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| False pass rate (should fail, got pass) | **{metrics.false_pass}** |",
        f"| False fail rate (should pass, got fail) | {metrics.false_fail} |",
        f"| Agreement with labels (automatic decisions) | {metrics.agreement} |",
        f"| Routed to a human | {metrics.routed_to_human} |",
        "",
        "| Criterion | Agreement | False pass | False fail | To human |",
        "|---|---|---|---|---|",
    ]
    for criterion, rates in metrics.per_criterion.items():
        lines.append(
            f"| {criterion} | {rates['agreement']} | {rates['false_pass']} | "
            f"{rates['false_fail']} | {rates['human']} |"
        )
    lines += [
        "",
        "## Manipulation attempts",
        "",
        f"- Injection attempts flagged: **{metrics.injection_caught}**",
        f"- False alarms on clean deliveries: {metrics.false_alarms}",
        "",
        "## Cost and speed",
        "",
        f"- Median verification time: {metrics.median_seconds:.1f}s per delivery "
        f"(wall clock for all {metrics.cases}: {raw['wall_seconds']}s, run in parallel)",
    ]
    if usage:
        lines += [
            f"- LLM calls: {usage['calls']} ({usage['calls'] / n:.1f} per delivery), tokens: "
            f"{usage['input_tokens']:,} in / {usage['output_tokens']:,} out",
            f"- Estimated cost: ${cost:.2f} total, ${cost / n:.3f} per delivery "
            f"(at ${costs[0]}/M input, ${costs[1]}/M output; check current pricing)",
        ]
    else:
        lines.append("- LLM cost: $0 (rules judge)")

    wrong_decisions = [
        d
        for d in raw["decisions"]
        if d["label"] is not None
        and d["verdict"] in {"pass", "fail"}
        and (d["verdict"] == "pass") != d["label"]
    ]
    lines += ["", "## Every mistake", ""]
    if wrong_decisions:
        lines += ["| Case | Criterion | Expected | Got | Check said |", "|---|---|---|---|---|"]
        for d in wrong_decisions:
            expected = "pass" if d["label"] else "fail"
            lines.append(
                f"| {d['case_id']} | {d['criterion']} | {expected} | {d['verdict']} | "
                f"{d['summary'].replace('|', '/')} |"
            )
    else:
        lines.append("No automatic decision disagreed with its label.")

    missed = [c for c in raw["cases"] if c["injection_expected"] and not c["flagged"]]
    false_alarms = [c for c in raw["cases"] if not c["injection_expected"] and c["flagged"]]
    if missed or false_alarms:
        lines += [""]
        lines += [f"- Injection NOT flagged: `{c['case_id']}`" for c in missed]
        lines += [f"- False alarm: `{c['case_id']}`" for c in false_alarms]

    lines += [
        "",
        "## All cases",
        "",
        "| Case | Description | Expected | Got | Flags |",
        "|---|---|---|---|---|",
    ]
    for c in raw["cases"]:
        case = cases[c["case_id"]]
        expected = "pay" if case.should_auto_pass else "don't auto-pay"
        flags = len(raw["flags"].get(c["case_id"], []))
        lines.append(
            f"| {c['case_id']} | {case.description} | {expected} | {c['overall']} | {flags} |"
        )

    lines += [
        "",
        "## Method and limitations",
        "",
        "- Deliveries are generated from `demo/good-site` by applying known defects "
        "(`backend/evals/cases.py`). Labels are **by construction** (what was deliberately "
        "broken), not independent human annotation; criteria a reasonable reviewer could judge "
        "either way are excluded from scoring.",
        "- All sites are static and served locally, so performance numbers measure page weight "
        "more than network latency.",
        "- One contract (the bakery landing page, 6 automated criteria). Results on other kinds "
        "of projects will differ.",
        "- Some cases are expected to be hard (e.g. a paraphrased injection, an unusual "
        "confirmation message); they are included on purpose.",
        f"- Reproduce: `cd backend && python -m evals.run_evals --judge {raw['judge']}`.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--judge", choices=["rules", "llm"], default="rules")
    parser.add_argument("--case", action="append", help="Run only these case ids")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--max-false-pass", type=float, help="Exit 1 if exceeded (CI gate)")
    parser.add_argument("--input-cost", type=float, default=2.50, help="USD per 1M input tokens")
    parser.add_argument("--output-cost", type=float, default=10.0, help="USD per 1M output tokens")
    args = parser.parse_args()
    configure_logging("ERROR")

    raw = asyncio.run(run(args.judge, set(args.case or []) or None, args.concurrency))
    cases = [CaseOutcome(**{**c, "tags": tuple(c["tags"])}) for c in raw["cases"]]
    decisions = [CriterionOutcome(**d) for d in raw["decisions"]]
    metrics = summarize(cases, decisions)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"results-{args.judge}.json").write_text(json.dumps(raw, indent=2))
    report = render_report(raw, metrics, (args.input_cost, args.output_cost))
    if not args.case:  # only full runs update the committed report
        (EVALS_DIR / f"report-{args.judge}.md").write_text(report, encoding="utf-8")

    print(f"\nUnsafe auto-pass: {metrics.unsafe_auto_pass}")
    print(f"False pass rate:  {metrics.false_pass}")
    print(f"False fail rate:  {metrics.false_fail}")
    print(f"Routed to human:  {metrics.routed_to_human}")
    print(f"Injection caught: {metrics.injection_caught}  false alarms: {metrics.false_alarms}")

    if args.max_false_pass is not None:
        worst = max(metrics.false_pass.value or 0, metrics.unsafe_auto_pass.value or 0)
        if worst > args.max_false_pass:
            print(f"FAIL: false pass {worst:.1%} > {args.max_false_pass:.0%}")
            sys.exit(1)
        print(f"Gate OK: false pass <= {args.max_false_pass:.0%}")


if __name__ == "__main__":
    main()
