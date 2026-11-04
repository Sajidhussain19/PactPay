"""Eval metrics. Pure functions over outcomes, so they're unit-tested and reproducible."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CriterionOutcome:
    case_id: str
    criterion: str
    label: bool | None  # True = should pass, False = should fail, None = not scored
    verdict: str  # pass | fail | needs_review | manual
    summary: str = ""


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    should_auto_pass: bool
    overall: str  # passed | failed | needs_review | error
    injection_expected: bool
    flagged: bool
    duration_ms: int
    tags: tuple[str, ...] = ()


@dataclass
class Rate:
    hits: int
    total: int

    @property
    def value(self) -> float | None:
        return self.hits / self.total if self.total else None

    def __str__(self) -> str:
        if not self.total:
            return "n/a"
        return f"{self.value:.1%} ({self.hits}/{self.total})"


@dataclass
class EvalMetrics:
    cases: int
    decisions: int
    # Criterion level
    agreement: Rate  # among decisions the system made automatically (pass/fail)
    false_pass: Rate  # should fail, got pass  -> paying for bad work
    false_fail: Rate  # should pass, got fail  -> unfair to the freelancer
    routed_to_human: Rate
    # Case level (what actually happens to the money)
    unsafe_auto_pass: Rate  # defective or manipulated, yet auto-PASSED
    good_auto_passed: Rate  # honest and complete, auto-PASSED
    good_wrongly_failed: Rate  # honest and complete, but FAILED
    # Security
    injection_caught: Rate
    false_alarms: Rate
    per_criterion: dict[str, dict[str, Rate]] = field(default_factory=dict)
    median_seconds: float = 0.0


def summarize(cases: list[CaseOutcome], decisions: list[CriterionOutcome]) -> EvalMetrics:
    scored = [d for d in decisions if d.label is not None]
    automatic = [d for d in scored if d.verdict in {"pass", "fail"}]
    should_fail = [d for d in scored if d.label is False]
    should_pass = [d for d in scored if d.label is True]
    risky = [c for c in cases if not c.should_auto_pass]
    honest = [c for c in cases if c.should_auto_pass]
    injected = [c for c in cases if c.injection_expected]
    clean = [c for c in cases if not c.injection_expected]

    per_criterion: dict[str, dict[str, Rate]] = {}
    for criterion in sorted({d.criterion for d in scored}):
        mine = [d for d in scored if d.criterion == criterion]
        mine_auto = [d for d in mine if d.verdict in {"pass", "fail"}]
        per_criterion[criterion] = {
            "agreement": Rate(sum(_correct(d) for d in mine_auto), len(mine_auto)),
            "false_pass": Rate(
                sum(d.verdict == "pass" for d in mine if d.label is False),
                sum(d.label is False for d in mine),
            ),
            "false_fail": Rate(
                sum(d.verdict == "fail" for d in mine if d.label is True),
                sum(d.label is True for d in mine),
            ),
            "human": Rate(sum(d.verdict == "needs_review" for d in mine), len(mine)),
        }

    durations = sorted(c.duration_ms for c in cases)
    median = durations[len(durations) // 2] / 1000 if durations else 0.0

    return EvalMetrics(
        cases=len(cases),
        decisions=len(scored),
        agreement=Rate(sum(_correct(d) for d in automatic), len(automatic)),
        false_pass=Rate(sum(d.verdict == "pass" for d in should_fail), len(should_fail)),
        false_fail=Rate(sum(d.verdict == "fail" for d in should_pass), len(should_pass)),
        routed_to_human=Rate(sum(d.verdict == "needs_review" for d in scored), len(scored)),
        unsafe_auto_pass=Rate(sum(c.overall == "passed" for c in risky), len(risky)),
        good_auto_passed=Rate(sum(c.overall == "passed" for c in honest), len(honest)),
        good_wrongly_failed=Rate(sum(c.overall == "failed" for c in honest), len(honest)),
        injection_caught=Rate(sum(c.flagged for c in injected), len(injected)),
        false_alarms=Rate(sum(c.flagged for c in clean), len(clean)),
        per_criterion=per_criterion,
        median_seconds=median,
    )


def _correct(d: CriterionOutcome) -> bool:
    return (d.verdict == "pass") == d.label
