"""Exhaustive state machine tests: every (action, state) pair is either legal or rejected."""

from dataclasses import replace

import pytest

from app.workflows.milestone_state import (
    TERMINAL_STATES,
    TRANSITIONS,
    Action,
    IllegalTransitionError,
    MilestoneFacts,
    MilestoneState,
    Role,
    allowed_actions,
    next_state,
)

S = MilestoneState

# Facts that satisfy every guard, so these tests isolate source-state and role rules.
ALL_GUARDS_MET = MilestoneFacts(
    state=S.DRAFT,
    contract_agreed=True,
    authorization_id="AUTH1",
    has_submission=True,
    capture_id=None,
    payout_succeeded=True,
    authorization_voided=True,
)


def facts_for(action: Action, state: MilestoneState) -> MilestoneFacts:
    facts = replace(ALL_GUARDS_MET, state=state)
    if action is Action.RELEASE_COMPLETED:
        facts = replace(facts, capture_id="CAP1")  # release needs a capture; refund must not
    return facts


def a_permitted_role(action: Action) -> Role:
    return sorted(TRANSITIONS[action].roles)[0]


LEGAL = [(a, s) for a, t in TRANSITIONS.items() for s in sorted(t.sources)]
ILLEGAL = [(a, s) for a, t in TRANSITIONS.items() for s in S if s not in t.sources]


@pytest.mark.parametrize(("action", "state"), LEGAL)
def test_every_legal_transition(action: Action, state: MilestoneState) -> None:
    result = next_state(facts_for(action, state), action, a_permitted_role(action))
    assert result is TRANSITIONS[action].target


@pytest.mark.parametrize(("action", "state"), ILLEGAL)
def test_every_illegal_transition_is_rejected(action: Action, state: MilestoneState) -> None:
    with pytest.raises(IllegalTransitionError, match="only allowed from"):
        next_state(facts_for(action, state), action, a_permitted_role(action))


@pytest.mark.parametrize(
    ("action", "state", "role"),
    [
        (a, s, r)
        for a, t in TRANSITIONS.items()
        for s in sorted(t.sources)[:1]
        for r in Role
        if r not in t.roles
    ],
)
def test_every_unpermitted_role_is_rejected(
    action: Action, state: MilestoneState, role: Role
) -> None:
    with pytest.raises(IllegalTransitionError, match="not allowed"):
        next_state(facts_for(action, state), action, role)


def test_terminal_states_have_no_way_out() -> None:
    for state in TERMINAL_STATES:
        for role in Role:
            assert allowed_actions(facts_for(Action.AGREE, state), role) == []


# ----------------------------------------------------------------- guards


@pytest.mark.parametrize(
    ("action", "state", "missing", "reason"),
    [
        (Action.AGREE, S.DRAFT, {"contract_agreed": False}, "approve the contract"),
        (Action.FUND, S.AGREED, {"authorization_id": None}, "no PayPal authorization"),
        (Action.SUBMIT, S.FUNDED, {"has_submission": False}, "no deliverable"),
        (Action.APPROVE_RELEASE, S.PASSED, {"authorization_id": None}, "no PayPal authorization"),
        (Action.RELEASE_COMPLETED, S.RELEASING, {"capture_id": None}, "not been captured"),
        (Action.RELEASE_COMPLETED, S.RELEASING, {"payout_succeeded": False}, "payout"),
        (Action.REFUND, S.FUNDED, {"authorization_voided": False}, "not been voided"),
        (Action.REFUND, S.FAILED, {"capture_id": "CAP1"}, "already captured"),
        (Action.RESOLVE_REFUND, S.DISPUTED, {"authorization_voided": False}, "not been voided"),
    ],
)
def test_guards_block_unsafe_money_moves(
    action: Action, state: MilestoneState, missing: dict[str, object], reason: str
) -> None:
    facts = replace(facts_for(action, state), **missing)  # type: ignore[arg-type]
    with pytest.raises(IllegalTransitionError, match=reason):
        next_state(facts, action, a_permitted_role(action))


# --------------------------------------------------------- key money rules


def test_cannot_release_without_passing() -> None:
    for state in S:
        if state is S.PASSED:
            continue
        with pytest.raises(IllegalTransitionError):
            next_state(
                facts_for(Action.APPROVE_RELEASE, state), Action.APPROVE_RELEASE, Role.CLIENT
            )


def test_freelancer_can_never_release_money() -> None:
    passed = facts_for(Action.APPROVE_RELEASE, S.PASSED)
    assert Action.APPROVE_RELEASE not in allowed_actions(passed, Role.FREELANCER)
    assert Action.APPROVE_RELEASE in allowed_actions(passed, Role.CLIENT)


def test_happy_path_end_to_end() -> None:
    steps = [
        (Action.AGREE, Role.SYSTEM, S.AGREED),
        (Action.FUND, Role.SYSTEM, S.FUNDED),
        (Action.SUBMIT, Role.FREELANCER, S.SUBMITTED),
        (Action.START_VERIFICATION, Role.SYSTEM, S.VERIFYING),
        (Action.VERIFICATION_FAILED, Role.SYSTEM, S.FAILED),
        (Action.SUBMIT, Role.FREELANCER, S.SUBMITTED),  # fix and resubmit
        (Action.START_VERIFICATION, Role.SYSTEM, S.VERIFYING),
        (Action.VERIFICATION_PASSED, Role.SYSTEM, S.PASSED),
        (Action.APPROVE_RELEASE, Role.CLIENT, S.RELEASING),
        (Action.RELEASE_COMPLETED, Role.SYSTEM, S.RELEASED),
    ]
    facts = replace(ALL_GUARDS_MET, capture_id="CAP1")
    for action, role, expected in steps:
        new = next_state(facts, action, role)
        assert new is expected
        facts = replace(facts, state=new)


def test_allowed_actions_drive_ui_buttons() -> None:
    funded = facts_for(Action.SUBMIT, S.FUNDED)
    assert allowed_actions(funded, Role.FREELANCER) == [Action.SUBMIT]
    assert set(allowed_actions(funded, Role.CLIENT)) == {Action.REFUND}
