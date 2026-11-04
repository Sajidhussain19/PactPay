"""Run the full verification workflow (checks + AI judge) on a demo site.

cd backend
python -m scripts.verify_demo good-site
python -m scripts.verify_demo buggy-site
python -m scripts.verify_demo malicious-site
python -m scripts.verify_demo --url https://your-site.example --repo https://github.com/o/r
"""

import argparse
import asyncio

from app.agents.contract_schema import Contract
from app.agents.judge import LLMJudge, RuleJudge
from app.core.config import get_settings
from app.core.llm import build_llm
from app.core.logging import configure_logging
from app.security.scanner import SubmissionScanner
from app.verification.report import Submission, VerificationEvent, VerificationReport
from app.verification.runner import CheckRunner
from app.verification.web_checks import LLMSelectorSuggester, WebChecker
from app.workflows.verification_graph import VerificationWorkflow
from scripts.check_site import DEMO_DIR, MOCKUP_PNG, serve_demo

ICONS = {"pass": "PASS ", "fail": "FAIL ", "needs_review": "REVIEW", "manual": "MANUAL"}


async def print_event(event: VerificationEvent) -> None:
    if event.type == "check_finished":
        print(f"  ... {event.criterion_id} {event.check_type}: {event.status}")


def print_report(report: VerificationReport) -> None:
    print(
        f"\nOVERALL: {report.overall.upper()}   {report.counts}   {report.duration_ms / 1000:.1f}s"
    )
    for c in report.criteria:
        print(f"\n[{ICONS[c.verdict]}] {c.criterion_id} {c.description}")
        print(f"         confidence {c.confidence:.2f} by {c.decided_by}: {c.reasoning}")
        if c.fix_suggestion:
            print(f"         FIX: {c.fix_suggestion}")
    for flag in report.security_flags:
        label = f"SECURITY {flag.severity.upper()}"
        print(f"\n[{label}] {flag.pattern} in {flag.source}: {flag.snippet}")


async def run(url: str, repo: str | None) -> None:
    settings = get_settings()
    llm = build_llm(settings)
    contract = Contract.model_validate_json((DEMO_DIR / "contracts" / "bakery.json").read_text())
    mockup = MOCKUP_PNG.read_bytes() if MOCKUP_PNG.exists() else None
    artifacts = settings.artifacts_dir / "verify_demo"

    async with WebChecker(artifacts, locator=LLMSelectorSuggester(llm) if llm else None) as web:
        runner = CheckRunner(web, artifacts_dir=artifacts, llm=llm, mockup=mockup)
        workflow = VerificationWorkflow(
            runner,
            LLMJudge(llm) if llm else RuleJudge(),
            confidence_threshold=settings.judge_confidence_threshold,
            on_event=print_event,
            security_scan=SubmissionScanner(web, scan_repo=False),
        )
        print(f"Verifying {url}")
        report = await workflow.run(contract.milestones[0], Submission(live_url=url, repo_url=repo))
    print_report(report)
    out = artifacts / "report.json"
    out.write_text(report.model_dump_json(indent=2))
    print(f"\nFull report with evidence: {out.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("demo", nargs="?", default="good-site")
    parser.add_argument("--url")
    parser.add_argument("--repo")
    args = parser.parse_args()
    configure_logging("WARNING")
    url = args.url or f"{serve_demo()}/{args.demo}/"
    asyncio.run(run(url, args.repo))


if __name__ == "__main__":
    main()
