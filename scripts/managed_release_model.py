"""Dependency-free bounded release risk model; not an implementation proof.

Core functions are pure. Only main() serializes a receipt to stdout. See the
verification note for source mapping, atomicity assumptions and exclusions.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, replace
from typing import Iterator, TypeVar


VARIANTS = (
    "safe", "late_prepare", "boot_replay", "unknown_swap", "unknown_write",
    "observation_rollback", "cached_pin_auth", "close_with_request",
    "close_with_run", "swap_without_ready", "repin_request",
)


@dataclass(frozen=True)
class State:
    desired: int = 0
    boot: int = 0
    # Slot is an abstract unique attempt AND its exclusive generation.
    epochs: tuple[int, ...] = (0, 0)
    boots: tuple[int, ...] = (0, 0)
    preparing: tuple[bool, ...] = (False, False)
    resources: tuple[bool, ...] = (False, False)
    durable_ready: tuple[bool, ...] = (False, False)
    pointer: int = -1
    observed_epoch: int = 0
    observed_boot: int = -1
    pending: int = -1
    unknown: bool = False
    requests: tuple[int, ...] = (0, 0)
    runs: tuple[int, ...] = (0, 0)
    retiring: tuple[bool, ...] = (False, False)
    closed: tuple[bool, ...] = (False, False)
    authorized: bool = True


T = TypeVar("T")


def put(values: tuple[T, ...], index: int, value: T) -> tuple[T, ...]:
    """Immutable update; tuple element types are maintained by callers."""
    return values[:index] + (value,) + values[index + 1:]


@dataclass(frozen=True)
class Edge:
    action: str
    state: State
    slot: int = -1


def successors(s: State, variant: str = "safe") -> Iterator[Edge]:
    """Enumerate all bounded environment choices in stable order.

finish owns the entire synchronous owner boundary (ready append and swap);
provider completion is separate and can race desired acceptance. Unknown
append has both durable and absent outcomes; neither publishes a pointer.
"""
    if variant not in VARIANTS:
        raise ValueError("Unknown model variant")
    if s.desired < 2 and (not s.unknown or variant == "unknown_write"):
        yield Edge("accept_intent", replace(s, desired=s.desired + 1))
    yield Edge("change_authority", replace(s, authorized=not s.authorized))
    if s.unknown:
        # Abstract verified close/reopen of SAME store, not missing-receipt inference.
        yield Edge("recover_owner", replace(s, unknown=False))
    if s.boot == 0:
        # Process crash discards volatile handles, not a graceful resource close.
        pointer = s.pointer if variant == "boot_replay" else -1
        yield Edge("new_boot", replace(s, boot=1, pointer=pointer, pending=-1,
            preparing=(False, False), resources=(False, False),
            requests=(0, 0), runs=(0, 0), retiring=(False, False),
            closed=(False, False), unknown=False))
    for i in range(2):
        if (s.desired and s.epochs[i] == 0 and not s.unknown
                and s.pending == -1 and s.authorized):
            yield Edge("start_prepare", replace(s,
                epochs=put(s.epochs, i, s.desired), boots=put(s.boots, i, s.boot),
                preparing=put(s.preparing, i, True)), i)
        if s.preparing[i] and not s.resources[i]:
            yield Edge("provider_ready", replace(s,
                resources=put(s.resources, i, True)), i)
            yield Edge("provider_failure", replace(s,
                preparing=put(s.preparing, i, False)), i)
        if s.preparing[i] and s.resources[i]:
            if not s.unknown and s.authorized:
                terminal = replace(s, preparing=put(s.preparing, i, False),
                    durable_ready=put(s.durable_ready, i, True))
                if s.epochs[i] != s.desired and variant != "late_prepare":
                    yield Edge("finish_superseded", replace(terminal,
                        retiring=put(s.retiring, i, True)), i)
                elif s.pending == -1:
                    retiring = s.retiring
                    if s.pointer != -1:
                        retiring = put(retiring, s.pointer, True)
                    ready = terminal.durable_ready
                    if variant == "swap_without_ready":
                        ready = put(ready, i, False)
                    requests = terminal.requests
                    if variant == "repin_request" and s.pointer != -1:
                        requests = put(requests, i, requests[s.pointer])
                        requests = put(requests, s.pointer, 0)
                    yield Edge("swap", replace(terminal, pointer=i, pending=i,
                        retiring=retiring, durable_ready=ready, requests=requests), i)
                else:
                    yield Edge("finish_pending_blocked", replace(terminal,
                        retiring=put(s.retiring, i, True)), i)
                for durable in (False, True):
                    yield Edge("ready_append_unknown", replace(s, unknown=True,
                        preparing=put(s.preparing, i, False),
                        durable_ready=put(s.durable_ready, i, durable),
                        retiring=put(s.retiring, i, True)), i)
            if s.unknown and variant == "unknown_swap":
                yield Edge("swap", replace(s, pointer=i, pending=i,
                    preparing=put(s.preparing, i, False)), i)
        if s.pending == i and not s.unknown:
            if s.authorized:
                yield Edge("observe", replace(s, observed_epoch=s.epochs[i],
                    observed_boot=s.boot, pending=-1), i)
            # Failure before append (e.g. authority) and uncertain append differ.
            pointer = -1 if variant == "observation_rollback" else s.pointer
            yield Edge("observation_failure", replace(s, pointer=pointer), i)
            for durable in (False, True):
                yield Edge("observation_append_unknown", replace(s, unknown=True,
                    observed_epoch=s.epochs[i] if durable else s.observed_epoch,
                    observed_boot=s.boot if durable else s.observed_boot), i)
        if s.pointer == i and not any(s.requests) and not s.closed[i]:
            yield Edge("request_pin", replace(s, requests=put(s.requests, i, 1)), i)
        if (s.pointer == i and not any(s.runs) and s.authorized
                and not s.retiring[i] and not s.closed[i]):
            yield Edge("run_retain", replace(s, runs=put(s.runs, i, 1)), i)
        if s.runs[i]:
            if not any(s.requests) and not s.closed[i]:
                yield Edge("run_child_pin", replace(s,
                    requests=put(s.requests, i, 1)), i)
            yield Edge("run_release_or_expire", replace(s,
                runs=put(s.runs, i, 0)), i)
        if s.requests[i]:
            if s.authorized or variant == "cached_pin_auth":
                # Atomic decision-return + synchronous dispatch prefix, no await.
                yield Edge("dispatch", s, i)
            yield Edge("request_finish", replace(s,
                requests=put(s.requests, i, 0)), i)
        if s.retiring[i] and not s.closed[i]:
            request_ok = not s.requests[i] or variant == "close_with_request"
            run_ok = not s.runs[i] or variant == "close_with_run"
            if request_ok and run_ok:
                yield Edge("close_resources", replace(s,
                    closed=put(s.closed, i, True)), i)


def violations(before: State, edge: Edge) -> tuple[str, ...]:
    """Independent safety predicates over actual pre/post state and operation.

No mutant flag is consulted here. Edge checks include self-loop dispatches;
checking only newly discovered vertices would miss cached-authority bugs.
"""
    after, i = edge.state, edge.slot
    errors: list[str] = []
    if edge.action == "swap":
        if after.epochs[i] != before.desired:
            errors.append("no_late_prepare_overwrite")
        if not after.durable_ready[i]:
            errors.append("durable_ready_before_swap")
        if before.requests != after.requests:
            errors.append("request_pin_is_stable")
    if edge.action == "new_boot" and after.pointer != -1:
        errors.append("new_boot_has_no_pointer")
    if before.unknown and edge.action in ("swap", "accept_intent", "observe"):
        errors.append("unknown_fences_swap_and_owner_writes")
    if edge.action.startswith("observation_") and before.pointer != after.pointer:
        errors.append("observation_failure_preserves_pointer")
    if edge.action == "dispatch" and not before.authorized:
        errors.append("pins_are_not_current_authority")
    if edge.action == "close_resources" and (before.requests[i] or before.runs[i]):
        errors.append("leases_prevent_resource_close")
    if edge.action != "new_boot":
        if any(after.closed[j] and (after.requests[j] or after.runs[j]) for j in range(2)):
            errors.append("closed_resources_have_no_leases")
    return tuple(errors)


@dataclass(frozen=True)
class Check:
    variant: str
    states: int
    transitions: int
    failures: tuple[str, ...]
    trace: tuple[Edge, ...]
    actions: tuple[str, ...]


def check(variant: str = "safe") -> Check:
    """Exhaustive BFS to fixpoint, or first violation (shortest edge count)."""
    initial = State()
    queue = deque([initial])
    parents: dict[State, tuple[State, Edge] | None] = {initial: None}
    transitions = 0
    actions: set[str] = set()
    while queue:
        before = queue.popleft()
        for edge in successors(before, variant):
            transitions += 1
            actions.add(edge.action)
            failures = violations(before, edge)
            if failures:
                path = [edge]
                cursor = before
                while (parent := parents[cursor]) is not None:
                    cursor, step = parent
                    path.append(step)
                return Check(variant, len(parents), transitions, failures,
                             tuple(reversed(path)), tuple(sorted(actions)))
            if edge.state not in parents:
                parents[edge.state] = (before, edge)
                queue.append(edge.state)
    return Check(variant, len(parents), transitions, (), (), tuple(sorted(actions)))


def receipt() -> dict[str, object]:
    """Deterministic logical receipt; intentionally excludes wall-clock timing."""
    results = [check(variant) for variant in VARIANTS]
    return {
        "schema": "managed-release-risk-model/v1",
        "runtime_scope": "Python abstract single-owner single-event-loop safety BFS",
        "bounds": {"epochs": 2, "attempts_generations": 2, "boots": 2,
                   "simultaneous_requests": 1, "simultaneous_runs": 1},
        "exhaustive_safe_fixpoint": not results[0].failures,
        "all_unsafe_variants_detected": all(r.failures for r in results[1:]),
        "results": [asdict(result) for result in results],
        "exclusions": ["native bindings", "hardware durability", "liveness",
                       "arbitrary provider correctness", "business write fencing"],
    }


def main() -> int:
    import json

    result = receipt()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if (result["exhaustive_safe_fixpoint"]
                 and result["all_unsafe_variants_detected"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())