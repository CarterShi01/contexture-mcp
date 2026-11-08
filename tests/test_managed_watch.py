"""Candidate native watch evidence over real strict FileJournal/GraphOwner.

No synthetic event source, business executor, serving coordinator or wire claim.
Only Host authentication/admission and monotonic time are explicit test ports.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from threading import Event

import pytest

from contexture.application.managed_watch import ManagedWatchService, WatchProfile
from contexture.core.graph.canonical import canonical_digest, canonical_json
from contexture.core.graph.errors import GraphError
from contexture.core.graph.history import History
from contexture.core.graph.policy import AdmissionDecision
from contexture.server.managed_journal import FileJournal
from tests.test_managed_owner import LATER, SCHEMA, Host, boot, owner, request
from tests.test_managed_runtime import setup, system as runtime_system


class WatchHost(Host):
    scope = frozenset({"r"})

    def decide(self, request):
        if request.operation == "graph.watch":
            assert self.fences > 0
            self.calls.append(request)
            return AdmissionDecision(not self.denied, self.context.authority_revision, self.scope)
        return super().decide(request)


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def system():
    with tempfile.TemporaryDirectory(prefix="contexture-watch-") as temp:
        with FileJournal.open(Path(temp) / "journal", schema=SCHEMA) as journal:
            host = WatchHost()
            manager = owner(journal, host)
            boot(manager)
            clock = Clock()
            watch = ManagedWatchService(manager, key=b"host-watch-key-" + b"x" * 32, clock=clock)
            yield manager, journal, host, clock, watch
            watch.shutdown()
            manager.close()


def commit(system, name, key=None):
    manager = system[0]
    manager.commit(manager.preview(request(manager, name=name, key=key or name), expires_at=LATER))


def name(material):
    return material["nodes"][0]["name"]


def resync(call):
    with pytest.raises(GraphError) as error:
        call()
    assert error.value.code == "resync_required"
    assert str(error.value) == str(GraphError(
        "resync_required", "Native watch unavailable; take a new authorized snapshot"))


def test_snapshot_tail_atomic_complete_batch_retry_and_no_effects(system):
    manager, journal, host, _, watch = system
    first = watch.start("g")
    commit(system, "second")
    # One transaction produces multiple facts, still exactly one descriptor.
    manager.commit(manager.preview(request(manager, key="multi", commands=[
        {"op": "rename_node", "node_id": "r", "new_name": "third"},
        {"op": "create_node", "node": {"node_id": "child", "kind": "role", "name": "child",
         "description": "Child", "instructions": "Never execute"}},
        {"op": "attach_node", "node_id": "child", "target": {
         "parent_id": "r", "slot": "children", "position": 0}}
    ]), expires_at=LATER))
    result = watch.poll(first["cursor"], graph_id="g")
    assert name(first["snapshot"]) == "root"
    assert [name(b["payload"]) for b in result["batches"]] == ["second", "third"]
    assert len(journal.records()[-1]["events"][0]["resolved_facts"]) > 1
    assert watch.poll(first["cursor"], graph_id="g") == result
    result["batches"][0]["payload"]["nodes"][0]["name"] = "caller mutation"
    assert name(watch.poll(first["cursor"], graph_id="g")["batches"][0]["payload"]) == "second"
    assert watch.poll(result["cursor"], graph_id="g")["batches"] == []
    assert journal.watermark == "3"
    assert set(r.operation for r in host.calls if "watch" in r.operation) == {"graph.watch"}


def test_concurrent_commits_snapshot_and_tail_no_skip(system):
    manager, _, _, _, watch = system
    entered, proceed = Event(), Event()
    proposal = manager.preview(request(manager, name="race", key="race"), expires_at=LATER)
    original = manager._admission.decide

    def decide(req):
        if req.operation == "graph.watch" and not entered.is_set():
            entered.set()
            assert proceed.wait(5)
        return original(req)

    manager._admission.decide = decide
    with ThreadPoolExecutor(max_workers=2) as pool:
        start = pool.submit(watch.start, "g")
        assert entered.wait(5)
        future = pool.submit(manager.commit, proposal)
        proceed.set()
        first = start.result()
        future.result()
    tail = watch.poll(first["cursor"], graph_id="g")
    observed = [name(first["snapshot"])] + [name(b["payload"]) for b in tail["batches"]]
    assert observed in (["root", "race"], ["race"])


def test_invisible_noop_other_graph_advance_without_counts_or_raw_hash(system):
    manager, journal, host, _, watch = system
    host.scope = frozenset()
    first = watch.start("g")
    commit(system, "hidden")
    commit(system, "hidden", "noop")
    boot(manager, "other")
    result = watch.poll(first["cursor"], graph_id="g")
    assert result["batches"] == [] and result["cursor"] != first["cursor"]
    assert watch.start("g")["view_digest"] == first["view_digest"]
    text = json.dumps([first, result])
    for record in journal.records():
        assert record["record_hash"] not in text
    for hidden in ("hidden", "other", "journal_seq", "revision", "count", "remaining", "caught_up"):
        assert hidden not in text
    assert watch.poll(result["cursor"], graph_id="g")["batches"] == []


@pytest.mark.parametrize("mode", ["deny", "scope", "actor", "tenant", "authority", "stopped", "closed"])
def test_each_poll_fresh_identity_authority_and_unknown_fail_closed(system, mode):
    manager, _, host, _, watch = system
    first = watch.start("g")
    if mode == "deny":
        host.denied = True
    elif mode == "scope":
        host.scope = frozenset()
    elif mode == "stopped":
        manager._stopped = True
    elif mode == "closed":
        manager.close()
    else:
        field = {"actor": "actor_id", "tenant": "tenant_id", "authority": "authority_revision"}[mode]
        host.context = replace(host.context, **{field: "2" if mode == "authority" else "other"})
    resync(lambda: watch.poll(first["cursor"], graph_id="g"))
    assert not watch._sessions


@pytest.mark.parametrize("mode", ["graph", "selection", "profile", "tamper", "unknown", "cross_service"])
def test_cursor_opaque_bound_and_tamper_rejected(system, mode):
    _, _, _, _, watch = system
    first = watch.start("g")
    token = first["cursor"]
    assert len(token) == 129
    kwargs = {"graph_id": "g"}
    if mode == "graph":
        kwargs["graph_id"] = "other"
    elif mode == "selection":
        kwargs["selection"] = frozenset({"r"})
    elif mode == "profile":
        kwargs["profile"] = WatchProfile(max_batches=1)
    elif mode == "tamper":
        token = token[:-1] + ("0" if token[-1] != "0" else "1")
    elif mode == "unknown":
        token = watch._token("unknown", "cursor", "1")
    else:
        watch = ManagedWatchService(system[0], key=b"host-watch-key-" + b"x" * 32)
    resync(lambda: watch.poll(token, **kwargs))


def test_retention_loss_origin_change_and_missing_event(system):
    manager, _, _, _, watch = system
    first = watch.start("g")
    commit(system, "next")
    history = manager._histories["g"]
    manager._histories["g"] = History.seed(history.head, journal_seq="2")
    resync(lambda: watch.poll(first["cursor"], graph_id="g"))
    manager._histories["g"] = history
    first = watch.start("g")
    commit(system, "last")
    current = manager._histories["g"]
    # Corrupted already-materialized state must not be interpreted as a delta.
    object.__setattr__(current.records[-1], "event", None)
    resync(lambda: watch.poll(first["cursor"], graph_id="g"))


def test_batch_limit_and_byte_boundary_never_skip_last_batch(system):
    _, _, _, _, watch = system
    profile = WatchProfile(max_batches=1)
    first = watch.start("g", profile=profile)
    for i in range(3):
        commit(system, f"change-{i}")
    cursor, names = first["cursor"], []
    for _ in range(3):
        result = watch.poll(cursor, graph_id="g", profile=profile)
        assert len(result["batches"]) == 1
        names.append(name(result["batches"][0]["payload"]))
        cursor = result["cursor"]
    assert names == ["change-0", "change-1", "change-2"]
    # One descriptor fits, two do not; especially exercise the final-batch
    # byte-boundary path (which must NOT advance directly to the watermark).
    small = WatchProfile(max_bytes=1000, inline_bytes=100)
    start = watch.start("g", profile=small)
    commit(system, "one-more")
    commit(system, "two-more")
    a = watch.poll(start["cursor"], graph_id="g", profile=small)
    b = watch.poll(a["cursor"], graph_id="g", profile=small)
    assert len(a["batches"]) == len(b["batches"]) == 1
    assert name(watch.fetch(a["batches"][0]["authorized_fetch_ref"], graph_id="g", profile=small)) == "one-more"
    assert name(watch.fetch(b["batches"][0]["authorized_fetch_ref"], graph_id="g", profile=small)) == "two-more"


def test_exact_large_fetch_after_new_commit_retry_digest_and_revocation(system):
    _, _, host, _, watch = system
    profile = WatchProfile(inline_bytes=1)
    first = watch.start("g", profile=profile)
    commit(system, "original large batch")
    descriptor = watch.poll(first["cursor"], graph_id="g", profile=profile)["batches"][0]
    assert "payload" not in descriptor
    token = descriptor["authorized_fetch_ref"]
    commit(system, "later head must not replace fetch")
    material = watch.fetch(token, graph_id="g", profile=profile)
    assert name(material) == "original large batch"
    assert watch.fetch(token, graph_id="g", profile=profile) == material
    assert descriptor["size"] == len(canonical_json(material))
    assert descriptor["facts_digest"] == canonical_digest(material, "contexture.view/v1")
    host.scope = frozenset()
    resync(lambda: watch.fetch(token, graph_id="g", profile=profile))
    assert not watch._sessions


@pytest.mark.parametrize("mode", ["expiry", "time", "handles", "retained_bytes", "payload", "shutdown"])
def test_limits_expiry_and_cleanup_no_background_queue(system, mode):
    _, _, host, clock, watch = system
    profile = WatchProfile(inline_bytes=1)
    first = watch.start("g", profile=profile, ttl=5)
    commit(system, "large")
    if mode == "expiry":
        clock.now = 5
    elif mode == "time":
        original = host.decide

        def delayed(req):
            if req.operation == "graph.watch":
                clock.now += 2
            return original(req)

        host.decide = delayed
    elif mode == "handles":
        watch.max_handles = 1
    elif mode == "retained_bytes":
        watch.max_retained_bytes = 1
    elif mode == "payload":
        watch.max_payload_bytes = 1
    else:
        watch.shutdown()
    resync(lambda: watch.poll(first["cursor"], graph_id="g", profile=profile))
    assert not watch._sessions


def test_real_reopen_replayed_history_without_handler_execution(system):
    manager, journal, host, _, watch = system
    first = watch.start("g")
    commit(system, "persisted")
    expected = watch.poll(first["cursor"], graph_id="g")
    path = journal.root
    watch.shutdown()
    manager.close()
    journal.close()
    with FileJournal.open(path, schema=SCHEMA) as reopened:
        recovered = owner(reopened, host)
        new = ManagedWatchService(recovered, key=b"x" * 32)
        assert name(new.start("g")["snapshot"]) == name(expected["batches"][-1]["payload"])
        resync(lambda: new.poll(first["cursor"], graph_id="g"))
        new.shutdown()
        recovered.close()


def test_origin_and_out_of_owner_durable_advance_resync(system):
    manager, journal, _, _, watch = system
    first = watch.start("g")
    session = next(iter(watch._sessions.values()))
    session.origin = ("other-store", "other-origin-hash")
    resync(lambda: watch.poll(first["cursor"], graph_id="g"))
    first = watch.start("g")
    event = journal.records()[-1]["events"][0]
    # Real external durable append: owner must report unknown, not expose tail.
    journal.append([event], actor_ref="tenant:actor")
    resync(lambda: watch.poll(first["cursor"], graph_id="g"))
    assert manager.writes_stopped


def test_non_metadata_changes_do_not_leak_digest_or_facts(system):
    manager, _, _, _, watch = system
    first = watch.start("g")
    manager.commit(manager.preview(request(manager, key="program-change", commands=[
        {"op": "update_node", "node_id": "r", "set_fields": {
            "instructions": "Secret program that must never appear"}, "unset_fields": []}
    ]), expires_at=LATER))
    tail = watch.poll(first["cursor"], graph_id="g")
    assert tail["batches"] == []
    assert first["view_digest"] == watch.start("g")["view_digest"]
    assert "Secret" not in json.dumps(tail)


def test_fetch_retention_loss_invalidates_all_material(system):
    manager, _, _, _, watch = system
    profile = WatchProfile(inline_bytes=1)
    first = watch.start("g", profile=profile)
    commit(system, "fetchable")
    descriptor = watch.poll(first["cursor"], graph_id="g", profile=profile)["batches"][0]
    head = manager._histories["g"].head
    manager._histories["g"] = History.seed(head, journal_seq="2")
    resync(lambda: watch.fetch(descriptor["authorized_fetch_ref"], graph_id="g", profile=profile))
    assert not watch._sessions


@pytest.mark.parametrize("options", [
    {"max_batches": 0}, {"max_batches": 1025}, {"max_bytes": 0},
    {"inline_bytes": 1024 * 1024 + 1}, {"max_seconds": float("inf")},
    {"max_seconds": 0}, {"max_batches": True}])
def test_invalid_profile_budgets_rejected(options):
    with pytest.raises(ValueError):
        WatchProfile(**options)


def test_snapshot_capacity_and_expiry_sweep(system):
    manager, _, _, clock, _ = system
    watch = ManagedWatchService(manager, key=b"x" * 32, clock=clock, max_sessions=1)
    first = watch.start("g", ttl=1)
    resync(lambda: watch.start("g"))
    clock.now = 1
    watch.sweep()
    assert not watch._sessions
    watch.start("g")
    resync(lambda: watch.poll(first["cursor"], graph_id="g"))
    watch.shutdown()


@pytest.mark.asyncio
async def test_watch_has_no_business_handler_or_resource_effects(runtime_system):
    coordinator, _, _ = await setup(runtime_system)
    manager, _, execution, _, _ = runtime_system
    watch = ManagedWatchService(manager, key=b"x" * 32)
    try:
        first = watch.start("g")
        watch.poll(first["cursor"], graph_id="g")
        assert not execution.calls and not execution.closed
    finally:
        watch.shutdown()
        await coordinator.shutdown()