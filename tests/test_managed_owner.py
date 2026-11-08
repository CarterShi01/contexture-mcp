"""Owner integration with the actual strict FileJournal on native temp storage.

Exercise the public application package without synthetic module registration.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any

import pytest

from contexture.application.managed import EventProjection, GraphOwner
from contexture.core.graph.canonical import canonical_json, freeze_json
from contexture.core.graph.errors import GraphError
from contexture.core.graph.policy import (
    AdmissionDecision, AdmissionRequest, VerifiedApproval, VerifiedContext,
)
from contexture.core.graph.transactions import Limits
from contexture.server.managed_journal import CommitUnknown, FileJournal

ROOT = Path(__file__).parents[1]
SCHEMA = json.loads((ROOT / "spec/managed/schemas/managed.schema.json").read_text())
NOW = "2026-11-05T00:00:00Z"
LATER = "2026-11-05T01:00:00Z"


class Host:
    def __init__(self):
        self.lock = RLock()
        self.context = VerifiedContext("actor", "tenant", "1",
                                      frozenset({"graph.roots", "graph.bootstrap"}))
        self.denied = False
        self.bootstrap_denied = False
        self.approval_required = False
        self.proof_denied = False
        self.now = NOW
        self.calls = []
        self.fences = 0

    def current(self):
        return self.context

    def actor_ref(self, context):
        return f"{context.tenant_id}:{context.actor_id}"

    @contextmanager
    def hold(self, context):
        with self.lock:
            assert context == self.context
            self.fences += 1
            try:
                yield
            finally:
                self.fences -= 1

    def decide(self, request):
        assert self.fences > 0
        self.calls.append(request)
        allowed = not self.denied and not (self.bootstrap_denied and "bootstrap" in request.operation)
        return AdmissionDecision(allowed, self.context.authority_revision,
            frozenset((*request.impact, *request.reference_targets)),
            self.approval_required and request.operation != "graph.read")

    def verify(self, context, reference, subject_digest, now):
        assert self.fences > 0
        if self.proof_denied:
            raise GraphError("approval_stale", "Approval revoked")
        return VerifiedApproval(context.actor_id, context.tenant_id,
                                context.authority_revision, subject_digest, LATER)


def seed(graph_id="g", *, mutable=True):
    return {"schema": "contexture.bootstrap/v1", "graph_id": graph_id,
        "expected_revision": "0", "seed": {"inline": {
            "schema": "contexture.graph/v1", "semantic_version": "1", "graph_id": graph_id,
            "application_name": "app", "mode": "disclosure_only",
            "nodes": [{"node_id": "r", "kind": "role", "name": "root",
                       "description": "root", "instructions": "program", "mutable": mutable}],
            "roots": [{"node_id": "r", "plane": "model"}],
            "presentations": {"default_locale": "en", "catalogs": [{"locale": "en", "entries": []}]}}},
        "approval_ref": "approved", "idempotency_key": f"seed-{graph_id}",
        "reason": "trusted seed", "correlation_id": "trace"}


def request(owner, *, key="key", name="renamed", graph_id="g", commands=None):
    return {"schema": "contexture.transaction/v1", "graph_id": graph_id,
        "base_revision": owner.read(graph_id).revision, "idempotency_key": key,
        "commands": commands or [{"op": "rename_node", "node_id": "r", "new_name": name}],
        "reason": "test", "correlation_id": "trace"}


def owner(journal, host, **kwargs):
    return GraphOwner(journal, identities=host, admission=host, authority_fence=host,
                      clock=lambda: host.now, approvals=host, **kwargs)


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory(prefix="contexture-owner-") as temp:
        path = Path(temp) / "store"
        with FileJournal.open(path, schema=SCHEMA) as journal:
            yield path, journal, Host()


def boot(manager, graph_id="g", **kwargs):
    return manager.commit_bootstrap(manager.preview_bootstrap(seed(graph_id, **kwargs), expires_at=LATER))


def test_actual_file_journal_bootstrap_preview_commit_reopen(store):
    path, journal, host = store
    manager = owner(journal, host)
    initial = manager.preview_bootstrap(canonical_json(seed()), expires_at=LATER)
    assert journal.watermark == "0"
    result = manager.commit_bootstrap(initial)
    assert result["revision"] == result["journal_seq"] == "1"
    assert result["actor_ref"] == "tenant:actor" and result["durability"] == "strict"
    previous = manager.read("g")
    transaction = request(manager)
    proposal = manager.preview(transaction, expires_at=LATER)
    transaction["commands"][0]["new_name"] = "caller mutation"
    assert manager.read("g") is previous and journal.watermark == "1"
    result = manager.commit(proposal)
    assert result["revision"] == "2" and previous.definition.nodes["r"].name == "root"
    expected = manager.export("g")
    manager.close()
    journal.close()
    host.denied = True  # Replay is pure and must not consult even this policy.
    host.calls.clear()
    with FileJournal.open(path, schema=SCHEMA) as reopened:
        recovered = owner(reopened, host)
        assert not host.calls
        host.denied = False
        assert recovered.export("g") == expected
        assert recovered.receipt("g", "key") == result
        assert len(recovered.history("g").records) == 2
        recovered.close()


def test_incremental_owner_receipts_do_not_copy_prefix(store):
    class NoCopyDict(dict):
        def __iter__(self):
            raise AssertionError("Full accepted receipt index copied")

        def keys(self):
            raise AssertionError("Full accepted receipt index copied")

    _, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    original = manager.history("g")
    manager._receipts = NoCopyDict(manager._receipts)
    for i in range(1, 101):
        proposal = manager.preview(request(manager, key=f"edit-{i}", name=f"edit-{i}"), expires_at=LATER)
        assert manager.commit(proposal)["revision"] == str(i + 1)
    assert len(original.records) == 1 and original.head.revision == "1"
    assert manager.receipt("g", "edit-100")["revision"] == "101"
    assert len(manager.history("g").records) == 101


def test_partial_owner_receipt_publication_preserves_prefix_until_reopen(store):
    class FailingDict(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            raise MemoryError("receipt publication")

    path, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    proposal = manager.preview(request(manager), expires_at=LATER)
    previous_history = manager._histories["g"]
    previous_receipts = dict(manager._receipts)
    manager._receipts = FailingDict(manager._receipts)
    with pytest.raises(MemoryError):
        manager.commit(proposal)
    assert manager.writes_stopped and manager._watermark == "1"
    assert manager._histories["g"] is previous_history
    assert manager._receipts == previous_receipts
    assert journal.watermark == "2"
    with pytest.raises(GraphError):
        manager.commit(proposal)
    manager.close()
    journal.close()
    with FileJournal.open(path, schema=SCHEMA) as reopened:
        recovered = owner(reopened, host)
        assert recovered.read("g").revision == "2"
        assert recovered.commit(proposal) == recovered.receipt("g", "key")
        recovered.close()


def test_idempotency_before_cas_and_expiry_current_read_only(store):
    _, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    first = manager.preview(request(manager), expires_at=LATER)
    accepted = manager.commit(first)
    second = manager.preview(request(manager, key="next", name="next"), expires_at=LATER)
    manager.commit(second)
    host.now = LATER
    host.context = replace(host.context, authority_revision="2")
    host.calls.clear()
    assert manager.commit(first) == accepted
    assert [r.operation for r in host.calls] == ["graph.read"]
    changed = replace(first, prepared=replace(first.prepared,
        envelope=type(first.prepared.envelope).from_json({
            **dict(first.prepared.envelope.value), "reason": "different"})))
    with pytest.raises(GraphError) as error:
        manager.commit(changed)
    assert error.value.code == "idempotency_conflict"
    host.denied = True
    with pytest.raises(GraphError) as error:
        manager.commit(first)
    assert error.value.code == "permission_denied"
    assert journal.watermark == "3"


def test_actor_tenant_scope_and_no_payload_credentials(store):
    _, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    proposal = manager.preview(request(manager), expires_at=LATER)
    manager.commit(proposal)
    host.context = replace(host.context, tenant_id="other")
    assert manager.receipt("g", "key") is None
    with pytest.raises(GraphError):
        manager.commit(proposal)
    bad = request(manager, key="other-key")
    bad["actor_ref"] = "tenant:actor"
    with pytest.raises(GraphError):
        manager.preview(bad, expires_at=LATER)
    assert journal.watermark == "2"


@pytest.mark.parametrize("change", ["cas", "expiry", "authority", "deny", "proof"])
def test_current_commit_revalidation_and_no_partial_publish(store, change):
    _, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    transaction = request(manager)
    transaction["approval_ref"] = "approved"
    proposal = manager.preview(transaction, expires_at=LATER)
    if change == "cas":
        manager.commit(manager.preview(request(manager, key="other", name="other"), expires_at=LATER))
    elif change == "expiry": host.now = LATER
    elif change == "authority": host.context = replace(host.context, authority_revision="2")
    elif change == "deny": host.denied = True
    elif change == "proof": host.proof_denied = True
    before = journal.watermark
    with pytest.raises(GraphError): manager.commit(proposal)
    assert journal.watermark == before
    assert not manager.writes_stopped


def test_noop_durable_receipt_advances_sequence_only(store):
    _, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    accepted = manager.commit(manager.preview(request(manager, name="root"), expires_at=LATER))
    assert accepted["status"] == "noop" and accepted["revision"] == "1"
    assert accepted["journal_seq"] == "2" and journal.watermark == "2"
    assert journal.records()[-1]["events"][0]["type"] == "CommandCompleted"
    assert [r.state.revision for r in manager.history("g").records] == ["1", "1"]


def test_single_owner_binding_and_thread_serialized_cas(store):
    _, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    with pytest.raises(GraphError) as error: owner(journal, host)
    assert error.value.code == "store_in_use"
    proposals = [manager.preview(request(manager, key=str(i), name=str(i)), expires_at=LATER)
                 for i in range(8)]
    def commit_one(proposal):
        try: return manager.commit(proposal)["status"]
        except GraphError as exc: return exc.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(commit_one, proposals))
    assert outcomes.count("committed") == 1 and outcomes.count("revision_conflict") == 7
    assert journal.watermark == "2"
    manager.close()
    replacement = owner(journal, host)
    assert replacement.read("g").revision == "2"
    replacement.close()


@pytest.mark.parametrize("stage", ["record.before_rename", "record.renamed", "record.directory_synced"])
def test_fault_no_head_publication_stop_release_and_recover_pending_receipt(store, stage):
    path, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    proposal = manager.preview(request(manager), expires_at=LATER)
    before = manager.read("g")
    def fault(step):
        if step == stage: raise OSError("fault")
    journal._fault = fault
    with pytest.raises((OSError, CommitUnknown)):
        manager.commit(proposal)
    assert manager.read("g") is before
    assert manager.writes_stopped
    with pytest.raises(GraphError) as error:
        with manager.release_boundary(): pass
    assert error.value.code == "commit_unknown"
    with pytest.raises(GraphError): manager.commit(proposal)
    manager.close()
    journal.close()
    with FileJournal.open(path, schema=SCHEMA) as reopened:
        recovered = owner(reopened, host)
        if stage == "record.before_rename":
            assert recovered.read("g").revision == "1"
            assert recovered.receipt("g", "key") is None
            accepted = recovered.commit(proposal)
        else:
            assert recovered.read("g").revision == "2"
            accepted = recovered.receipt("g", "key")
        assert recovered.commit(proposal) == accepted
        assert reopened.watermark == "2"
        recovered.close()


def test_delete_restore_and_revert_retained_tombstones_reopen(store):
    path, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    commands = [{"op": "create_node", "node": {"node_id": "a", "kind": "role",
        "name": "a", "description": "a", "instructions": "a"}},
        {"op": "attach_node", "node_id": "a", "target": {
            "parent_id": "r", "slot": "children", "position": 0}}]
    manager.commit(manager.preview(request(manager, key="create", commands=commands), expires_at=LATER))
    commands = [{"op": "delete_nodes", "targets": ["a"], "deleted_ids": ["a"],
        "policy": "reject_references", "disconnections": [
            {"source_id": "r", "target_id": "a", "relation": "containment"}]}]
    manager.commit(manager.preview(request(manager, key="delete", commands=commands), expires_at=LATER))
    tombstones = manager.read("g").tombstones
    assert tombstones[0]["deleted_revision"] == "3"
    revert = manager.preview_revert("g", "2", idempotency_key="revert", reason="restore",
                                   correlation_id="trace", expires_at=LATER)
    manager.commit(revert)
    assert manager.read("g").revision == "4" and "a" in manager.read("g").definition.nodes
    assert manager.read("g").tombstones == tombstones
    manager.close()
    journal.close()
    with FileJournal.open(path, schema=SCHEMA) as reopened:
        recovered = owner(reopened, host)
        assert recovered.read("g").tombstones == tombstones
        assert recovered.read("g", "3").definition.nodes.keys() == {"r"}
        assert recovered.export("g", "2") == recovered.export("g", "4")
        recovered.close()


@pytest.mark.parametrize("bad", ["raw_duplicate", "unknown", "capability", "approval", "admission", "budget", "identity"])
def test_strict_bootstrap_gates(store, bad):
    _, journal, host = store
    kwargs = {"limits": Limits(max_definition_bytes=10)} if bad == "budget" else {}
    manager = owner(journal, host, **kwargs)
    envelope = seed()
    if bad == "raw_duplicate":
        envelope = b'{"schema":"duplicate",' + canonical_json(envelope)[1:]
    elif bad == "unknown": envelope["trusted"] = True
    elif bad == "capability": host.context = replace(host.context, host_permissions=frozenset({"graph.roots"}))
    elif bad == "approval": host.proof_denied = True
    elif bad == "admission": host.bootstrap_denied = True
    elif bad == "identity": envelope["graph_id"] = "other"
    with pytest.raises(GraphError): manager.preview_bootstrap(envelope, expires_at=LATER)
    assert journal.watermark == "0"


def test_bootstrap_exact_provenance_retry_and_multiple_graphs(store):
    path, journal, host = store
    manager = owner(journal, host)
    proposal = manager.preview_bootstrap(seed(), expires_at=LATER)
    accepted = manager.commit_bootstrap(proposal)
    boot(manager, "second")
    host.now = LATER
    assert manager.commit_bootstrap(proposal) == accepted
    event = journal.records()[0]["events"][0]
    assert event["migration"]["kind"] == "bootstrap"
    assert event["migration"]["approval_ref"] == "approved"
    assert event["resolved_facts"][0]["before"] is None
    manager.close()
    journal.close()
    with FileJournal.open(path, schema=SCHEMA) as reopened:
        recovered = owner(reopened, host)
        assert recovered.read("g").revision == recovered.read("second").revision == "1"
        recovered.close()


def test_protection_root_authority_budget_and_nonmutation(store):
    _, journal, host = store
    manager = owner(journal, host)
    boot(manager, mutable=False)
    before = manager.export("g")
    with pytest.raises(GraphError) as error:
        manager.preview(request(manager), expires_at=LATER)
    assert error.value.code == "mutation_protected"
    assert manager.export("g") == before and journal.watermark == "1"
    manager.close()
    small = owner(journal, host, limits=Limits(max_transaction_bytes=10))
    with pytest.raises(GraphError) as error: small.preview(request(small), expires_at=LATER)
    assert error.value.code == "limit_exceeded"
    small.close()


def release_projection():
    def reduce(state, event, record):
        return {"history": [*state["history"], {"event": event, "seq": record["journal_seq"]}]}
    return {"release": EventProjection(frozenset({"ReleaseRequested"}), freeze_json({"history": []}), reduce)}


def release_event(manager):
    state = manager.read("g")
    return {"type": "ReleaseRequested", "release_epoch": "1", "request": {
        "schema": "contexture.release-request/v1", "graph_id": "g", "target_id": "target",
        "revision": state.revision, "definition_digest": state.definition_digest,
        "manifest_digest": "sha256:" + "1" * 64, "expected_desired_epoch": "0",
        "idempotency_key": "release-key", "policy": "exact", "reason": "test"}}


def test_release_projection_registration_no_ignored_records(store):
    path, journal, host = store
    manager = owner(journal, host, projections=release_projection())
    boot(manager)
    event = release_event(manager)
    state = manager.read("g")
    admission = AdmissionRequest(host.context, "g", "release.request", (), state.revision,
        state.definition_digest, state.definition_digest, ("r",), (), None, None)
    with manager.release_boundary() as boundary:
        boundary.append([event], request=admission, correlation_id="trace")
        boundary.check()
    with pytest.raises(GraphError): boundary.check()
    expected = manager.projection("release", graph_id="g")
    assert len(expected["history"]) == 1
    manager.close()
    journal.close()
    with FileJournal.open(path, schema=SCHEMA) as reopened:
        with pytest.raises(GraphError) as error: owner(reopened, host)
        assert error.value.code == "unsupported_contract"
        recovered = owner(reopened, host, projections=release_projection())
        assert recovered.projection("release", graph_id="g") == expected
        assert reopened.watermark == "2"
        recovered.close()


def test_unregistered_future_event_rejects_not_drop(store):
    _, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    state = manager.read("g")
    req = AdmissionRequest(host.context, "g", "release.request", (), state.revision,
        state.definition_digest, state.definition_digest, (), (), None, None)
    with manager.release_boundary() as boundary:
        with pytest.raises(GraphError) as error:
            boundary.append([{"type": "FutureRequiredEvent"}], request=req, correlation_id="trace")
        assert error.value.code == "unsupported_contract"
    assert journal.watermark == "1"


def test_reads_history_export_and_receipt_recheck_current_authority(store):
    _, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    host.denied = True
    for read in [lambda: manager.read("g"), lambda: manager.read("g", "1"),
                 lambda: manager.history("g"), lambda: manager.export("g"),
                 lambda: manager.receipt("g", "seed-g")]:
        with pytest.raises(GraphError) as error: read()
        assert error.value.code == "permission_denied"


def test_root_operations_require_current_independent_permission(store):
    _, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    host.context = replace(host.context, host_permissions=frozenset({"graph.bootstrap"}))
    commands = [{"op": "create_node", "node": {"node_id": "new", "kind": "role",
        "name": "new", "description": "new", "instructions": "new", "mutable": True}},
        {"op": "add_root", "node_id": "new", "plane": "model", "position": 1}]
    with pytest.raises(GraphError) as error:
        manager.preview(request(manager, commands=commands), expires_at=LATER)
    assert error.value.code == "permission_denied" and journal.watermark == "1"


def test_release_boundary_cannot_be_used_from_another_thread(store):
    _, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    with manager.release_boundary() as boundary:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(boundary.check)
            with pytest.raises(GraphError) as error: future.result()
            assert error.value.code == "resource_busy"
        boundary.check()


def test_current_approval_can_be_added_at_commit_without_mutating_preview(store):
    _, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    proposal = manager.preview(request(manager), expires_at=LATER)
    host.approval_required = True
    with pytest.raises(GraphError) as error: manager.commit(proposal)
    assert error.value.code == "approval_stale"
    result = manager.commit(proposal, approval_ref="approved")
    assert result["revision"] == "2"


def test_external_journal_advance_stops_owner_and_release(store):
    _, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    journal.append([{"type": "CommandCompleted", "graph_id": "g", "revision": "1",
        "command_digest": "sha256:" + "1" * 64, "status": "noop"}], actor_ref="external")
    with pytest.raises(GraphError) as error: manager.read("g")
    assert error.value.code == "commit_unknown" and manager.writes_stopped


@pytest.mark.parametrize("callback", ["current", "actor_ref", "hold", "decide", "verify", "clock"])
@pytest.mark.parametrize("nested", ["read", "commit"])
@pytest.mark.parametrize("propagate", [False, True])
def test_callback_reentry_refused_before_nested_ports_and_append(store, monkeypatch, callback, nested, propagate):
    _, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    transaction = request(manager)
    transaction["approval_ref"] = "approved"
    proposal = manager.preview(transaction, expires_at=LATER)
    before = journal.watermark
    attempted = False
    blocked = []

    def reenter():
        nonlocal attempted
        if attempted:
            return
        attempted = True
        calls, fences = len(host.calls), host.fences
        try:
            if nested == "read":
                manager.read("g")
            else:
                manager.commit(proposal)
        except GraphError as exc:
            assert exc.code == "resource_busy"
            blocked.append(exc.code)
            assert journal.watermark == before
            assert len(host.calls) == calls and host.fences == fences
            if propagate:
                raise
        else:
            pytest.fail("Nested operation entered owner callbacks or appended")

    if callback == "hold":
        original = host.hold
        @contextmanager
        def fenced(context):
            reenter()
            with original(context):
                yield
        monkeypatch.setattr(host, "hold", fenced)
    else:
        target, name = (manager, "_clock") if callback == "clock" else (host, callback)
        original = getattr(target, name)
        def hooked(*args, **kwargs):
            reenter()
            return original(*args, **kwargs)
        monkeypatch.setattr(target, name, hooked)

    if propagate:
        with pytest.raises(GraphError) as error:
            manager.commit(proposal)
        assert error.value.code == "resource_busy" and journal.watermark == before
    else:
        assert manager.commit(proposal)["revision"] == "2"
    assert blocked == ["resource_busy"]
    # Guard cleanup also permits safe retry after a propagated port failure.
    assert manager.commit(proposal)["revision"] == "2"
    assert manager.read("g").definition.nodes["r"].name == "renamed"
    assert journal.watermark == "2"


def test_operation_guard_clears_after_baseexception_and_fence_exit(store, monkeypatch):
    _, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    original = host.hold
    @contextmanager
    def failing(context):
        with original(context):
            yield
            with pytest.raises(GraphError) as error:
                manager.read("g")
            assert error.value.code == "resource_busy"
            raise KeyboardInterrupt("fence exit")
    with monkeypatch.context() as patch:
        patch.setattr(host, "hold", failing)
        with pytest.raises(KeyboardInterrupt):
            manager.read("g")
    assert manager.read("g").revision == "1"


def test_boundary_origin_requires_first_record_and_tracks_same_store_reopen(store):
    path, journal, host = store
    manager = owner(journal, host)
    with manager.release_boundary(write=False) as boundary:
        with pytest.raises(GraphError) as error:
            _ = boundary.store_origin
        assert error.value.code == "not_ready"
    boot(manager)
    with manager.release_boundary() as boundary:
        origin = boundary.store_origin
        assert origin == (journal.records()[0]["store_id"], journal.records()[0]["record_hash"])
        boundary.snapshot("g")  # Internal boundary calls remain usable.
    with pytest.raises(GraphError):
        _ = boundary.store_origin
    manager.close()
    journal.close()
    with FileJournal.open(path, schema=SCHEMA) as reopened:
        recovered = owner(reopened, host)
        with recovered.release_boundary(write=False) as boundary:
            assert boundary.store_origin == origin
        recovered.close()