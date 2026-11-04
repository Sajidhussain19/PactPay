from pathlib import Path

import pytest

from evals.cases import CASES, CRITERIA, Case
from evals.metrics import CaseOutcome, CriterionOutcome, summarize


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_every_case_builds(case: Case, tmp_path: Path) -> None:
    case.build(tmp_path / case.id)  # mutations assert their anchors exist
    assert (tmp_path / case.id / "index.html").exists()


def test_case_set_is_balanced() -> None:
    honest = [c for c in CASES if c.should_auto_pass]
    assert 40 <= len(CASES) <= 60
    assert len(honest) >= 15  # enough honest work to measure false fails
    assert sum(c.injection for c in CASES) >= 10
    for criterion in CRITERIA:  # every criterion has at least one known failure
        assert any(c.label(criterion) is False for c in CASES), criterion


def test_labels() -> None:
    case = Case("x", "x", fails=frozenset({"M1-C3"}), unscored=frozenset({"M1-C6"}))
    assert case.label("M1-C1") is True
    assert case.label("M1-C3") is False
    assert case.label("M1-C6") is None
    assert not case.should_auto_pass
    assert not Case("y", "y", injection=True).should_auto_pass


def decision(label: bool | None, verdict: str, criterion: str = "M1-C1") -> CriterionOutcome:
    return CriterionOutcome("case", criterion, label, verdict)


def case(
    should_pass: bool, overall: str, injection: bool = False, flagged: bool = False
) -> CaseOutcome:
    return CaseOutcome("case", should_pass, overall, injection, flagged, 1000)


def test_criterion_rates() -> None:
    metrics = summarize(
        [case(True, "passed")],
        [
            decision(True, "pass"),
            decision(True, "fail"),  # false fail
            decision(False, "fail"),
            decision(False, "pass"),  # false pass
            decision(False, "needs_review"),  # routed to human, not a false pass
            decision(None, "pass"),  # unscored: ignored everywhere
        ],
    )
    assert (metrics.false_pass.hits, metrics.false_pass.total) == (1, 3)
    assert (metrics.false_fail.hits, metrics.false_fail.total) == (1, 2)
    assert (metrics.agreement.hits, metrics.agreement.total) == (2, 4)
    assert (metrics.routed_to_human.hits, metrics.routed_to_human.total) == (1, 5)


def test_case_rates() -> None:
    metrics = summarize(
        [
            case(True, "passed"),
            case(True, "failed"),
            case(False, "passed"),  # unsafe auto-pass
            case(False, "needs_review", injection=True, flagged=True),
            case(False, "failed", injection=True, flagged=False),
            case(True, "passed", flagged=True),  # false alarm
        ],
        [],
    )
    assert str(metrics.unsafe_auto_pass) == "33.3% (1/3)"
    assert str(metrics.good_auto_passed) == "66.7% (2/3)"
    assert str(metrics.good_wrongly_failed) == "33.3% (1/3)"
    assert str(metrics.injection_caught) == "50.0% (1/2)"
    assert str(metrics.false_alarms) == "25.0% (1/4)"


def test_empty_rate_prints_na() -> None:
    assert str(summarize([], []).false_pass) == "n/a"
