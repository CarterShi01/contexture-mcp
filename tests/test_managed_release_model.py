"""WP02 pure model checks, independent of concurrently running runtime tests."""

from __future__ import annotations

from dataclasses import replace
import json

import pytest

from scripts.managed_release_model import (
    VARIANTS, Edge, State, check, main, receipt, successors, violations,
)


@pytest.fixture(scope="module")
def safe_check():
    return check()


def test_safe_model_reaches_complete_fixpoint_and_exercises_risks(safe_check):
    assert safe_check.failures == ()
    assert safe_check.states > 100
    assert safe_check.transitions > safe_check.states
    assert set(safe_check.actions) >= {
        "swap", "finish_superseded", "ready_append_unknown", "observe",
        "observation_failure", "observation_append_unknown", "recover_owner",
        "new_boot", "run_child_pin", "run_release_or_expire", "dispatch",
        "close_resources", "provider_failure", "change_authority",
    }


EXPECTED = {
    "late_prepare": "no_late_prepare_overwrite",
    "boot_replay": "new_boot_has_no_pointer",
    "unknown_swap": "unknown_fences_swap_and_owner_writes",
    "unknown_write": "unknown_fences_swap_and_owner_writes",
    "observation_rollback": "observation_failure_preserves_pointer",
    "cached_pin_auth": "pins_are_not_current_authority",
    "close_with_request": "leases_prevent_resource_close",
    "close_with_run": "leases_prevent_resource_close",
    "swap_without_ready": "durable_ready_before_swap",
    "repin_request": "request_pin_is_stable",
}


@pytest.mark.parametrize("variant,invariant", EXPECTED.items())
def test_mutations_have_replayable_shortest_counterexamples(variant, invariant):
    result = check(variant)
    assert invariant in result.failures
    assert result.trace
    before = State()
    for step in result.trace[:-1]:
        assert step in tuple(successors(before, variant))
        assert violations(before, step) == ()
        before = step.state
    final = result.trace[-1]
    assert final in tuple(successors(before, variant))
    assert invariant in violations(before, final)

    # Independently enumerate every shorter path layer, including self-loops.
    # This verifies minimal length, not merely that the returned trace is bad.
    frontier = {State()}
    visited = set(frontier)
    for _ in range(len(result.trace) - 1):
        following: set[State] = set()
        for state in frontier:
            for edge in successors(state, variant):
                assert violations(state, edge) == ()
                if edge.state not in visited:
                    following.add(edge.state)
        visited.update(following)
        frontier = following


def test_observation_is_not_pointer_and_failed_prepare_preserves_lkg():
    state = State(desired=2, epochs=(1, 2), boots=(0, 0),
        pointer=0, preparing=(False, True), resources=(True, False),
        durable_ready=(True, False), observed_epoch=1, observed_boot=0)
    failed = next(e for e in successors(state) if e.action == "provider_failure")
    assert failed.state.pointer == 0
    reboot = next(e for e in successors(state) if e.action == "new_boot")
    assert reboot.state.pointer == -1
    assert reboot.state.observed_epoch == 1
    assert reboot.state.durable_ready == (True, False)


def test_unknown_allows_existing_reads_and_provider_finish_but_not_publication():
    state = State(desired=1, epochs=(1, 1), preparing=(False, True),
        pointer=0, resources=(True, False), durable_ready=(True, False), unknown=True)
    edges = tuple(successors(state))
    assert {e.action for e in edges} >= {"request_pin", "provider_ready", "recover_owner"}
    assert not {"swap", "accept_intent", "observe"} & {e.action for e in edges}
    pinned = next(e.state for e in edges if e.action == "request_pin")
    assert any(e.action == "dispatch" for e in successors(pinned))


def test_run_expiry_does_not_close_inflight_child_or_bypass_revocation():
    state = State(desired=2, epochs=(1, 2), pointer=1, resources=(True, True),
        durable_ready=(True, True), retiring=(True, False), runs=(1, 0))
    child = next(e.state for e in successors(state) if e.action == "run_child_pin")
    expired = next(e.state for e in successors(child) if e.action == "run_release_or_expire")
    assert expired.requests == (1, 0)
    assert not any(e.action == "close_resources" for e in successors(expired))
    revoked = replace(expired, authorized=False)
    assert not any(e.action == "dispatch" for e in successors(revoked))
    finished = next(e.state for e in successors(revoked) if e.action == "request_finish")
    assert any(e.action == "close_resources" for e in successors(finished))


def test_checker_does_not_only_validate_new_states():
    state = State(requests=(1, 0), authorized=False)
    assert violations(state, Edge("dispatch", state, 0)) == ("pins_are_not_current_authority",)


def test_model_input_and_receipt_are_deterministic(capsys, monkeypatch):
    assert tuple(EXPECTED) == VARIANTS[1:]
    assert tuple(successors(State())) == tuple(successors(State()))
    with pytest.raises(ValueError):
        tuple(successors(State(), "not-a-variant"))
    first = receipt()
    assert first == receipt()
    assert first["exhaustive_safe_fixpoint"] is True
    assert first["all_unsafe_variants_detected"] is True
    # Exercise serialization without repeating expensive checks a third time.
    monkeypatch.setattr("scripts.managed_release_model.receipt", lambda: first)
    assert main() == 0
    output = capsys.readouterr().out
    assert json.loads(output) == json.loads(json.dumps(first))
    assert output == json.dumps(first, sort_keys=True, separators=(",", ":")) + "\n"