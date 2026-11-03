"""Milestone state machine: the single source of truth for what may happen next.

Pure logic, no I/O. Every transition names who may trigger it and a guard that checks the
facts it depends on (e.g. you can't release money without a PayPal authorization). Money
moves only in RELEASING/REFUNDED transitions, and only after these checks pass.

    DRAFT -> AGREED -> FUNDED -> SUBMITTED -> VERIFYING -> PASSED -> RELEASING -> RELEASED
                         |          ^            |  \\-> FAILED --(resubmit)--^
                         |          |            \\--> NEEDS_REVIEW -> PASSED | FAILED
                         |          \\-- verification error
                         \\-> REFUNDED (void)        PASSED | FAILED | NEEDS_REVIEW -> DISPUTED
    AGREED -> CANCELLED (nothing was held)          DISPUTED -> RELEASING | REFUNDED
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class MilestoneState(StrEnum):
    DRAFT = "draft"
    AGREED = "agreed"
    FUNDED = "funded"  # PayPal authorization holds the client's money
    SUBMITTED = "submitted"
    VERIFYING = "verifying"
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    DISPUTED = "disputed"
    RELEASING = "releasing"  # capture and/or payout in progress (retryable)
    RELEASED = "released"  # captured and paid out
    REFUNDED = "refunded"  # authorization voided
    CANCELLED = "cancelled"  # cancelled before any money was held


TERMINAL_STATES = frozenset(
    {MilestoneState.RELEASED, MilestoneState.REFUNDED, MilestoneState.CANCELLED}
)


class Action(StrEnum):
    AGREE = "agree"
    FUND = "fund"
    SUBMIT = "submit"
    START_VERIFICATION = "start_verification"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_FAILED = "verification_failed"
    VERIFICATION_UNCERTAIN = "verification_uncertain"
    VERIFICATION_ERROR = "verification_error"
    REVIEW_PASS = "review_pass"
    REVIEW_FAIL = "review_fail"
    APPROVE_RELEASE = "approve_release"
    RELEASE_COMPLETED = "release_completed"
    DISPUTE = "dispute"
    RESOLVE_RELEASE = "resolve_release"
    RESOLVE_REFUND = "resolve_refund"
    CANCEL = "cancel"
    REFUND = "refund"


class Role(StrEnum):
    CLIENT = "client"
    FREELANCER = "freelancer"
    ADMIN = "admin"
    SYSTEM = "system"  # verifier, webhooks, scheduler (e.g. auto-approve timeout)


@dataclass(frozen=True)
class MilestoneFacts:
    """What the guards are allowed to look at."""

    state: MilestoneState
    contract_agreed: bool = False
    authorization_id: str | None = None
    has_submission: bool = False
    capture_id: str | None = None
    payout_succeeded: bool = False
    authorization_voided: bool = False


Guard = Callable[[MilestoneFacts], str | None]  # returns the reason it's blocked, or None


def _ok(_: MilestoneFacts) -> str | None:
    return None


def _contract_agreed(f: MilestoneFacts) -> str | None:
    return None if f.contract_agreed else "both parties must approve the contract first"


def _authorized(f: MilestoneFacts) -> str | None:
    return None if f.authorization_id else "no PayPal authorization is holding the funds"


def _submitted(f: MilestoneFacts) -> str | None:
    return None if f.has_submission else "no deliverable has been submitted"


def _paid_out(f: MilestoneFacts) -> str | None:
    if not f.capture_id:
        return "the payment has not been captured"
    if not f.payout_succeeded:
        return "the freelancer payout has not succeeded"
    return None


def _voided(f: MilestoneFacts) -> str | None:
    return None if f.authorization_voided else "the PayPal authorization has not been voided"


def _not_captured(f: MilestoneFacts) -> str | None:
    return "money was already captured; it can't be refunded by voiding" if f.capture_id else None


@dataclass(frozen=True)
class Transition:
    sources: frozenset[MilestoneState]
    target: MilestoneState
    roles: frozenset[Role]
    guard: Guard = _ok


S = MilestoneState
_PARTIES = frozenset({Role.CLIENT, Role.FREELANCER})
_REVIEWERS = frozenset({Role.CLIENT, Role.ADMIN})


def _t(sources: set[S], target: S, roles: set[Role], guard: Guard = _ok) -> Transition:
    return Transition(frozenset(sources), target, frozenset(roles), guard)


TRANSITIONS: dict[Action, Transition] = {
    Action.AGREE: _t({S.DRAFT}, S.AGREED, {Role.SYSTEM}, _contract_agreed),
    Action.FUND: _t({S.AGREED}, S.FUNDED, {Role.SYSTEM}, _authorized),
    Action.SUBMIT: _t({S.FUNDED, S.FAILED}, S.SUBMITTED, {Role.FREELANCER}, _submitted),
    Action.START_VERIFICATION: _t({S.SUBMITTED}, S.VERIFYING, {Role.SYSTEM}),
    Action.VERIFICATION_PASSED: _t({S.VERIFYING}, S.PASSED, {Role.SYSTEM}),
    Action.VERIFICATION_FAILED: _t({S.VERIFYING}, S.FAILED, {Role.SYSTEM}),
    Action.VERIFICATION_UNCERTAIN: _t({S.VERIFYING}, S.NEEDS_REVIEW, {Role.SYSTEM}),
    Action.VERIFICATION_ERROR: _t({S.VERIFYING}, S.SUBMITTED, {Role.SYSTEM}),
    Action.REVIEW_PASS: _t({S.NEEDS_REVIEW}, S.PASSED, _REVIEWERS),
    Action.REVIEW_FAIL: _t({S.NEEDS_REVIEW}, S.FAILED, _REVIEWERS),
    # SYSTEM approves only via the auto-approve timeout.
    Action.APPROVE_RELEASE: _t({S.PASSED}, S.RELEASING, {Role.CLIENT, Role.SYSTEM}, _authorized),
    Action.RELEASE_COMPLETED: _t({S.RELEASING}, S.RELEASED, {Role.SYSTEM}, _paid_out),
    Action.DISPUTE: _t({S.PASSED, S.FAILED, S.NEEDS_REVIEW}, S.DISPUTED, set(_PARTIES)),
    Action.RESOLVE_RELEASE: _t({S.DISPUTED}, S.RELEASING, {Role.ADMIN}, _authorized),
    Action.RESOLVE_REFUND: _t({S.DISPUTED}, S.REFUNDED, {Role.ADMIN, Role.SYSTEM}, _voided),
    Action.CANCEL: _t({S.DRAFT, S.AGREED}, S.CANCELLED, {Role.CLIENT, Role.ADMIN}),
    # Client cancels a funded milestone (before work passes): void the hold, then REFUNDED.
    Action.REFUND: _t(
        {S.FUNDED, S.FAILED},
        S.REFUNDED,
        {Role.CLIENT, Role.ADMIN, Role.SYSTEM},
        lambda f: _not_captured(f) or _voided(f),
    ),
}


class IllegalTransitionError(Exception):
    def __init__(self, action: Action, state: MilestoneState, reason: str) -> None:
        super().__init__(f"Cannot {action} from {state}: {reason}")
        self.action = action
        self.state = state
        self.reason = reason


def next_state(facts: MilestoneFacts, action: Action, role: Role) -> MilestoneState:
    """Return the state after `action`, or raise IllegalTransitionError explaining why not."""
    transition = TRANSITIONS[action]
    if facts.state not in transition.sources:
        allowed = ", ".join(sorted(transition.sources))
        raise IllegalTransitionError(action, facts.state, f"only allowed from {allowed}")
    if role not in transition.roles:
        raise IllegalTransitionError(action, facts.state, f"{role} is not allowed to do this")
    if reason := transition.guard(facts):
        raise IllegalTransitionError(action, facts.state, reason)
    return transition.target


def allowed_actions(facts: MilestoneFacts, role: Role) -> list[Action]:
    """Actions `role` could take right now (drives which buttons the UI shows)."""
    return [
        action
        for action, t in TRANSITIONS.items()
        if facts.state in t.sources and role in t.roles and t.guard(facts) is None
    ]
