"""Bounded WP08 release projection evidence; no orchestration integration."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
import pytest

from contexture.core.graph.canonical import DOMAINS, canonical_digest, canonical_json, freeze_json
from contexture.core.graph.errors import GraphError
from contexture.core.graph.releases import (
    AttemptIdentity, AttemptState, DesiredRelease, Observation, ReleaseIdentity,
    ReleaseEvent, ReleaseReceipt, ReleaseRequest, empty_release_state, import_release_state,
    reduce_release_event,
)

D = "sha256:" + "a" * 64
U = "12345678-1234-4234-8234-123456789abc"
V = "22345678-1234-4234-8234-123456789abc"
SCHEMA = json.loads((Path(__file__).parents[1] / "spec/managed/schemas/managed.schema.json").read_text())


def validate(name: str, value: object) -> None:
    Draft202012Validator({**SCHEMA, "$ref": f"#/$defs/{name}"},
                         format_checker=FormatChecker()).validate(value)


def request(epoch: int = 0, graph: str = "g", target: str = "t", **changes: Any) -> dict[str, Any]:
    return {"schema": "contexture.release-request/v1", "graph_id": graph, "target_id": target,
            "revision": "1", "definition_digest": D, "manifest_digest": D,
            "expected_desired_epoch": str(epoch), "idempotency_key": f"key-{graph}-{target}-{epoch}",
            "policy": "exact", "reason": "release", **changes}


def identity(epoch: int = 1, graph: str = "g", target: str = "t") -> dict[str, Any]:
    return {"graph_id": graph, "target_id": target, "release_epoch": str(epoch)}


def attempt(epoch: int = 1, **changes: Any) -> dict[str, Any]:
    return {"release": identity(epoch), "instance_id": "instance", "boot_id": U,
            "attempt_id": f"attempt-{epoch}", **changes}


def observation(epoch: int = 1, **changes: Any) -> dict[str, Any]:
    return {"release": identity(epoch), "instance_id": "instance", "boot_id": U,
            "generation_id": U, "revision": "1", "definition_digest": D,
            "manifest_digest": D, "observed_at": "2026-11-06T00:00:00Z", **changes}


def requested(epoch: int = 0, **changes: Any) -> dict[str, Any]:
    return {"type": "ReleaseRequested", "request": request(epoch, **changes), "release_epoch": str(epoch + 1)}


def ready(epoch: int = 1, **changes: Any) -> dict[str, Any]:
    return {"type": "ReleasePreparationSucceeded", "attempt": attempt(epoch),
            "generation_id": U, "manifest_digest": D, **changes}


def record(event: dict[str, Any], seq: int = 1, **changes: Any) -> dict[str, Any]:
    return {"journal_seq": str(seq), "actor_ref": "actor", "events": [event], **changes}


def step(state: Any, event: dict[str, Any], seq: int = 1, **changes: Any) -> Any:
    validate("Event", event)
    return reduce_release_event(state, event, record(event, seq, **changes))


def stream() -> list[dict[str, Any]]:
    return [requested(), ready(), {"type": "ReplicaObserved", "observation": observation()},
            {"type": "ReleaseSuperseded", "release": identity(), "superseded_by": "2"},
            requested(1, revision="3"), ready(1, attempt=attempt(attempt_id="late")),
            {"type": "ReplicaObserved", "observation": observation(2, revision="3", boot_id=V)},
            {"type": "ReplicaObserved", "observation": observation()},
            {"type": "ReleasePreparationFailed", "attempt": attempt(2),
             "diagnostics": [{"code": "prepare_failed", "location": "", "message": "redacted"}]}]


def replay(events: list[dict[str, Any]], state: Any = None, offset: int = 0) -> Any:
    state = empty_release_state() if state is None else state
    for i, event in enumerate(events, offset + 1):
        state = step(state, event, i)
    return state


def values() -> list[tuple[Any, dict[str, Any]]]:
    return [(ReleaseRequest, request()), (ReleaseIdentity, identity()), (AttemptIdentity, attempt()),
            (Observation, observation()), (DesiredRelease, {"request": request(), "release_epoch": "1"}),
            (AttemptState, {"identity": attempt(), "status": "ready", "generation_id": U}),
            (ReleaseReceipt, {"schema": "contexture.release-receipt/v1", "release": identity(),
                              "journal_seq": "1", "request_digest": D, "status": "requested"})]


@pytest.mark.parametrize("cls,value", values())
def test_strict_shared_shapes_and_deeply_frozen(cls: Any, value: dict[str, Any]) -> None:
    validate(cls.__name__, value)
    expected = deepcopy(value)
    typed = cls.from_json(value)
    assert cls.from_json(canonical_json(value)) == typed
    value.clear()
    assert typed.as_json() == expected
    detached = typed.as_json()
    detached.clear()
    assert typed.as_json() == expected
    with pytest.raises((FrozenInstanceError, AttributeError)):
        typed._data = {}
    with pytest.raises(TypeError):
        typed._data["bad"] = "bad"
    with pytest.raises(TypeError):
        cls()
    for key in expected:
        bad = {k: v for k, v in expected.items() if k != key}
        # Optional generation_id is not a required schema property.
        if key == "generation_id":
            continue
        with pytest.raises(GraphError):
            cls.from_json(bad)
    with pytest.raises(GraphError):
        cls.from_json({**expected, "secret-do-not-echo": True})


def test_hash_exact_m19_not_unregistered_request_domain() -> None:
    typed = ReleaseRequest.from_json(request(approval_ref="approval"))
    assert "contexture.release-request/v1" not in DOMAINS
    expected = "sha256:" + hashlib.sha256(
        b"contexture.command/v1\0" + canonical_json(typed.subject())).hexdigest()
    assert typed.digest() == expected
    assert ReleaseRequest.from_json(request(idempotency_key="another")).digest() == expected
    assert ReleaseRequest.from_json(request(reason="different")).digest() != expected
    validate("ReleaseSubject", dict(typed.subject()))


@pytest.mark.parametrize("field,bad", [("revision", "0"), ("revision", "01"), ("revision", True),
    ("expected_desired_epoch", "1\n"), ("revision", "1" * 21), ("graph_id", "é" * 129),
    ("target_id", ""), ("reason", ""), ("policy", "automatic"),
    ("definition_digest", "sha256:" + "A" * 64), ("manifest_digest", D + "\n")])
def test_invalid_requests(field: str, bad: Any) -> None:
    with pytest.raises(GraphError):
        ReleaseRequest.from_json(request(**{field: bad}))


@pytest.mark.parametrize("changes", [{"boot_id": U.upper()}, {"generation_id": U + "\n"},
    {"observed_at": "2026-02-30T00:00:00Z"}, {"observed_at": "2026-11-06T00:00:00+00:00"},
    {"observed_at": "2026-11-06"}, {"revision": 1}])
def test_invalid_observations(changes: dict[str, Any]) -> None:
    with pytest.raises(GraphError):
        Observation.from_json(observation(**changes))


def test_cas_same_epoch_conflict_and_independent_targets_graphs() -> None:
    state = step(empty_release_state(), requested())
    for event in [requested(), requested(reason="conflict"), {**requested(1), "release_epoch": "3"}]:
        with pytest.raises(GraphError, match="Invalid release") as exc:
            step(state, event, 2)
        assert exc.value.code == "revision_conflict"
    state = step(state, requested(target="other"), 2)
    state = step(state, requested(graph="other"), 3)
    state = step(state, requested(1, revision="99"), 4)
    assert len(state["desired_releases"]) == 3
    actual = {}
    for desired in state["desired_releases"]:
        key = (desired["request"]["graph_id"], desired["request"]["target_id"])
        actual[key] = desired["release_epoch"]
    assert actual == {("g", "t"): "2", ("g", "other"): "1", ("other", "t"): "1"}


def test_late_ready_old_epoch_old_boot_only_history_and_checkpoint_tail() -> None:
    events = stream()
    complete = replay(events)
    assert complete["desired_releases"][0]["release_epoch"] == "2"
    assert [a["status"] for a in complete["prepare_attempt_history"]] == ["ready", "ready", "failed"]
    assert [o["release"]["release_epoch"] for o in complete["observations_history"]] == ["1", "2", "1"]
    assert set(complete) == {"desired_releases", "release_operations", "prepare_attempt_history", "observations_history"}
    for split in range(len(events) + 1):
        prefix = replay(events[:split])
        checkpoint = import_release_state(json.loads(canonical_json(prefix)))
        assert replay(events[split:], checkpoint, split) == complete
    for section, name in [("desired_releases", "DesiredRelease"), ("release_operations", "ReleaseReceipt"),
                          ("prepare_attempt_history", "AttemptState"), ("observations_history", "Observation")]:
        for value in complete[section]:
            validate(name, json.loads(canonical_json(value)))


def test_schema_has_no_start_events_and_duplicate_terminal_refuses() -> None:
    state = replay([requested(), ready()])
    with pytest.raises(GraphError) as exc:
        step(state, ready(), 3)
    assert exc.value.code == "identity_conflict"
    with pytest.raises(GraphError) as exc:
        reduce_release_event(state, {"type": "ReleasePreparationStarted"}, {"journal_seq": "3", "actor_ref": "a"})
    assert exc.value.code == "semantic_unsupported"


def test_no_effects_and_frozen_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins
    import socket
    import time
    import uuid

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Replay attempted an effect")

    with monkeypatch.context() as m:
        for module, name in [(builtins, "open"), (socket, "socket"), (time, "time"),
                             (time, "monotonic"), (uuid, "uuid4"), (Path, "read_text")]:
            m.setattr(module, name, forbidden)
        state = empty_release_state()
        for i, event in enumerate(stream(), 1):
            state = reduce_release_event(state, freeze_json(event), freeze_json(record(event, i)))  # type: ignore[arg-type]
        assert len(state["observations_history"]) == 3  # type: ignore[arg-type]


@pytest.mark.parametrize("mutation", ["unknown", "missing", "digest", "duplicate", "nonterminal", "unknown-observation"])
def test_checkpoint_corruption_refusal(mutation: str) -> None:
    value: Any = json.loads(canonical_json(replay(stream())))
    if mutation == "unknown":
        value["active"] = {}
    elif mutation == "missing":
        del value["release_operations"]
    elif mutation == "digest":
        value["desired_releases"][0]["request"]["reason"] = "tampered"
    elif mutation == "duplicate":
        value["desired_releases"] *= 2
    elif mutation == "nonterminal":
        value["prepare_attempt_history"][0]["status"] = "preparing"
    else:
        value["observations_history"][0]["release"]["release_epoch"] = "9"
    with pytest.raises(GraphError):
        import_release_state(value)


def test_optional_inline_receipt_actor_seq_digest_ownership_and_tamper() -> None:
    event = requested()
    result = {"schema": "contexture.release-receipt/v1", "release": identity(),
              "journal_seq": "1", "request_digest": ReleaseRequest.from_json(request()).digest(), "status": "requested"}
    evidence = {"schema": "contexture.command-receipt/v1", "graph_id": "g", "actor_ref": "actor",
                "idempotency_key": request()["idempotency_key"], "request_digest": result["request_digest"],
                "revision": "1", "journal_seq": "1", "status": "committed", "durability": "strict",
                "operation_result": {"inline": result}}
    validate("CommandReceipt", evidence)
    expected = step(empty_release_state(), event, command_receipt=evidence)
    assert expected["release_operations"][0] == result
    # Receipt's authoring head can differ from the selected release revision.
    assert step(empty_release_state(), event, command_receipt={**evidence, "revision": "99"}) == expected
    for key, bad in [("actor_ref", "other"), ("journal_seq", "2"), ("request_digest", D),
                     ("idempotency_key", "other"), ("graph_id", "other")]:
        with pytest.raises(GraphError):
            step(empty_release_state(), event, command_receipt={**evidence, key: bad})
    with pytest.raises(GraphError):
        step(empty_release_state(), event, command_receipt={**evidence, "operation_result": {"inline": {**result, "journal_seq": "2"}}})
    with pytest.raises(GraphError) as exc:
        step(empty_release_state(), event, command_receipt={**evidence, "operation_result": {"blob": {}}})
    assert exc.value.code == "semantic_unsupported"


def test_record_hashes_membership_and_mutation_isolation() -> None:
    event = requested()
    rec = {**record(event), "schema": "contexture.journal-record/v1", "store_id": "store",
           "event_batch_id": "batch", "prev_record_hash": None, "written_at": "2026-11-06T00:00:00Z",
           "correlation_id": "correlation"}
    rec["payload_digest"] = canonical_digest({"events": [event]}, "contexture.journal-payload/v1")
    rec["record_hash"] = canonical_digest(rec, "contexture.journal-record/v1")
    validate("CommitRecord", rec)
    state = reduce_release_event(empty_release_state(), event, rec)
    before = canonical_json(state)
    event["request"]["reason"] = "after-author-mutation"
    assert canonical_json(state) == before
    with pytest.raises(GraphError):
        reduce_release_event(empty_release_state(), event, rec)
    with pytest.raises(GraphError):
        reduce_release_event(empty_release_state(), requested(), record(ready()))


def test_current_metadata_unknown_attempt_supersession_and_safe_errors() -> None:
    state = step(empty_release_state(), requested())
    for event in [ready(manifest_digest="sha256:" + "b" * 64), ready(9),
                  {"type": "ReplicaObserved", "observation": observation(revision="8")},
                  {"type": "ReleaseSuperseded", "release": identity(), "superseded_by": "1"}]:
        with pytest.raises(GraphError):
            step(state, event, 2)
    with pytest.raises(GraphError) as exc:
        ReleaseRequest.from_json({**request(), "secret-do-not-echo": object()})
    assert "secret-do-not-echo" not in str(exc.value)
    assert not exc.value.location


@pytest.mark.parametrize("event", stream())
def test_typed_event_exact_closed_required_and_schema(event: dict[str, Any]) -> None:
    validate("Event", event)
    value = ReleaseEvent.from_json(freeze_json(event))
    assert value.as_json() == event
    assert ReleaseEvent.from_json(canonical_json(event)) == value
    for key in event:
        with pytest.raises(GraphError):
            ReleaseEvent.from_json({k: v for k, v in event.items() if k != key})
    with pytest.raises(GraphError):
        ReleaseEvent.from_json({**event, "secret-extra": "never echo"})


@pytest.mark.parametrize("changes", [{"instance_id": ""}, {"attempt_id": "x" * 257},
    {"boot_id": "invalid"}, {"release": {**identity(), "release_epoch": "0"}},
    {"release": {**identity(), "extra": True}}])
def test_attempt_identity_refusal(changes: dict[str, Any]) -> None:
    with pytest.raises(GraphError):
        AttemptIdentity.from_json(attempt(**changes))


def test_failed_empty_diagnostics_unknown_fields_and_checkpoint_whole_schema() -> None:
    state = replay(stream())
    for diagnostics in [[], [{"code": "x", "location": "~bad", "message": ""}],
                        [{"code": "x", "location": "", "message": "", "secret-extra": True}]]:
        with pytest.raises(GraphError):
            ReleaseEvent.from_json({"type": "ReleasePreparationFailed", "attempt": attempt(),
                                    "diagnostics": diagnostics})
    checkpoint = {"schema": "contexture.checkpoint/v1", "store_id": "store", "last_seq": "9",
                  "last_record_hash": D, "graphs": [], **json.loads(canonical_json(state)),
                  "receipts_index": [], "projection_versions": {"releases": "1"}, "blob_refs": []}
    checkpoint["checkpoint_digest"] = canonical_digest(checkpoint, "contexture.checkpoint/v1")
    validate("Checkpoint", checkpoint)
    assert import_release_state({k: checkpoint[k] for k in state}) == state