"""Adversarial read evidence over the real owner and native strict journal."""

from __future__ import annotations

from dataclasses import replace
import json
import subprocess

import pytest

from contexture.application.managed_catalog import ManagedCatalogService
from contexture.application.managed_management import ManagedManagementService
from contexture.application.managed_paging import CursorSigner
from contexture.application.managed_watch import ManagedWatchService, WatchProfile
from contexture.core.graph.errors import GraphError
from contexture.core.graph.history import History
from contexture.core.graph.policy import AdmissionDecision
from tests.test_managed_cli import SECRET, command, project as project, run
from tests.test_managed_owner import LATER, boot, owner, request, store as store


@pytest.fixture
def secured(store):
    _, journal, host = store
    manager = owner(journal, host)
    boot(manager)
    original = host.decide

    def decide(req):
        if req.operation in {"graph.watch", "graph.catalog"}:
            assert host.fences > 0
            return AdmissionDecision(not host.denied, host.context.authority_revision, frozenset({"r"}))
        return original(req)

    host.decide = decide
    clock = [0.0]
    watch = ManagedWatchService(manager, key=b"security-regression-host-key-123456", clock=lambda: clock[0])
    try:
        yield manager, journal, host, clock, watch
    finally:
        watch.shutdown()
        manager.close()


def commit(secured, name="changed", key="change"):
    manager = secured[0]
    manager.commit(manager.preview(request(manager, name=name, key=key), expires_at=LATER))


def refused(call, code="resync_required"):
    with pytest.raises(GraphError) as error:
        call()
    assert error.value.code == code


@pytest.mark.parametrize("operation", ["start", "poll", "fetch"])
def test_watch_shutdown_in_admission_never_publishes_detached_session(secured, operation):
    _, _, host, _, watch = secured
    profile = WatchProfile(inline_bytes=1)
    first = watch.start("g", profile=profile)
    commit(secured)
    descriptor = watch.poll(first["cursor"], graph_id="g", profile=profile)["batches"][0]
    original = host.decide

    def decide(req):
        if req.operation == "graph.watch":
            watch.shutdown()
        return original(req)

    host.decide = decide
    calls = {
        "start": lambda: watch.start("g", profile=profile),
        "poll": lambda: watch.poll(first["cursor"], graph_id="g", profile=profile),
        "fetch": lambda: watch.fetch(descriptor["authorized_fetch_ref"], graph_id="g", profile=profile),
    }
    refused(calls[operation])
    assert not watch._sessions


@pytest.mark.parametrize("operation", ["poll", "fetch"])
def test_watch_reentry_revocation_cannot_return_detached_bytes(secured, operation):
    _, _, host, _, watch = secured
    profile = WatchProfile(inline_bytes=1)
    first = watch.start("g", profile=profile)
    commit(secured)
    descriptor = watch.poll(first["cursor"], graph_id="g", profile=profile)["batches"][0]
    original = host.decide
    entered = []

    def decide(req):
        if req.operation == "graph.watch" and not entered:
            entered.append(True)
            # Owner callback reentry is rejected before identity/admission;
            # that failed inner poll also revokes this watch session.
            refused(lambda: watch.poll(first["cursor"], graph_id="g", profile=profile))
        return original(req)

    host.decide = decide
    call = (lambda: watch.poll(first["cursor"], graph_id="g", profile=profile)) if operation == "poll" else (
        lambda: watch.fetch(descriptor["authorized_fetch_ref"], graph_id="g", profile=profile))
    refused(call)
    assert entered and not watch._sessions


@pytest.mark.parametrize("command", ["head", "status", "history", "diff", "receipt"])
def test_management_full_graph_reads_require_full_scope_not_metadata_scope(secured, command):
    manager, _, host, _, _ = secured
    commit(secured)
    original = host.decide

    def decide(req):
        if req.operation == "graph.read":
            return AdmissionDecision(True, host.context.authority_revision, frozenset())
        return original(req)

    host.decide = decide
    service = ManagedManagementService(manager, mode="read_only")
    args = {"graph_id": "g"}
    if command == "diff":
        args.update(before="1", after="2")
    if command == "receipt":
        args["idempotency_key"] = "change"
    try:
        refused(lambda: service.execute(command, args), "permission_denied")
    finally:
        service.close()


@pytest.mark.parametrize("mode", ["expiry", "deny", "actor", "tenant", "authority"])
def test_watch_retained_fetch_rechecks_current_auth_and_expiry(secured, mode):
    _, _, host, clock, watch = secured
    profile = WatchProfile(inline_bytes=1)
    first = watch.start("g", profile=profile, ttl=5)
    commit(secured)
    batch = watch.poll(first["cursor"], graph_id="g", profile=profile)["batches"][0]
    if mode == "expiry":
        clock[0] = 5
    elif mode == "deny":
        host.denied = True
    else:
        field = {"actor": "actor_id", "tenant": "tenant_id", "authority": "authority_revision"}[mode]
        host.context = replace(host.context, **{field: "2" if mode == "authority" else "other"})
    refused(lambda: watch.fetch(batch["authorized_fetch_ref"], graph_id="g", profile=profile))
    assert not watch._sessions


def test_watch_retention_seed_at_checkpoint_preserves_complete_next_batch(secured):
    manager, _, _, _, watch = secured
    first = watch.start("g")
    commit(secured, "second", "second")
    second = watch.poll(first["cursor"], graph_id="g")
    # A fresh session at the retained seed remains usable; earlier handles do
    # not confer authority to reconstruct a missing before projection.
    watch.shutdown()
    watch = ManagedWatchService(manager, key=b"retention-security-key-123456789012")
    try:
        current = watch.start("g")
        seed = manager._histories["g"].head
        commit(secured, "third", "third")
        tail = manager._histories["g"].records[-1]
        manager._histories["g"] = History((History.seed(seed, journal_seq="2").records[0], tail))
        result = watch.poll(current["cursor"], graph_id="g")
        assert [b["payload"]["nodes"][0]["name"] for b in result["batches"]] == ["third"]
        assert watch.poll(current["cursor"], graph_id="g") == result
        assert second["batches"][0]["payload"]["nodes"][0]["name"] == "second"
    finally:
        watch.shutdown()


@pytest.mark.asyncio
async def test_catalog_hidden_body_graph_and_digest_not_in_projected_streams(secured):
    manager, journal, _, clock, watch = secured
    manager.commit(manager.preview(request(manager, key="hidden", commands=[
        {"op": "create_node", "node": {"node_id": "hidden", "kind": "role",
         "name": "SECRET-HIDDEN-NAME", "description": "SECRET-HIDDEN-DESCRIPTION",
         "instructions": "SECRET-HIDDEN-BODY"}},
        {"op": "attach_node", "node_id": "hidden", "target": {
         "parent_id": "r", "slot": "children", "position": 0}},
    ]), expires_at=LATER))
    catalog = ManagedCatalogService(manager, signer=CursorSigner(b"catalog-security-key-123456789012"), clock=lambda: clock[0])
    try:
        first = await catalog.start("g")
        snapshot = watch.start("g")
        manager.commit(manager.preview(request(manager, key="hidden-edit", commands=[
            {"op": "update_node", "node_id": "hidden", "set_fields": {
             "description": "SECRET-CHANGED", "instructions": "SECRET-CHANGED-BODY"}, "unset_fields": []},
            {"op": "update_node", "node_id": "r", "set_fields": {
             "instructions": "SECRET-VISIBLE-NODE-BODY"}, "unset_fields": []},
        ]), expires_at=LATER))
        boot(manager, "SECRET-OTHER-GRAPH")
        second = await catalog.start("g")
        assert first["view_digest"] == second["view_digest"]
        assert snapshot["view_digest"] == watch.start("g")["view_digest"]
        assert watch.poll(snapshot["cursor"], graph_id="g")["batches"] == []
        pages = [await catalog.continuation(token, graph_id="g", source="head", stream=stream)
                 for stream, token in first["streams"].items()]
        assert [n["node_id"] for n in pages[0]["items"]] == ["r"]
        assert pages[1]["items"] == pages[2]["items"] == []
        wire = json.dumps([first, second, pages, snapshot])
        for forbidden in ("SECRET", "instructions", "definition_digest", "journal_seq", "count", "hidden"):
            assert forbidden not in wire
        for record in journal.records():
            assert record["record_hash"] not in wire
        assert manager._histories["g"].head.definition_digest not in wire
    finally:
        catalog.shutdown()


@pytest.mark.asyncio
async def test_catalog_expiry_during_materialization_returns_no_snapshot(secured, monkeypatch):
    import contexture.application.managed_catalog as module

    manager, _, _, clock, _ = secured
    original = module.metadata_catalog

    def materialize(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] = 5
        return result

    monkeypatch.setattr(module, "metadata_catalog", materialize)
    catalog = ManagedCatalogService(manager, signer=CursorSigner(b"catalog-security-key-123456789012"), clock=lambda: clock[0])
    try:
        with pytest.raises(GraphError) as error:
            await catalog.start("g", ttl=5)
        assert error.value.code == "resync_required"
        assert catalog.counts == {"snapshots": 0, "pending": 0, "bytes": 0, "cursors": 0}
    finally:
        catalog.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["expiry", "deny", "actor", "tenant", "authority", "rotation"])
async def test_catalog_retained_page_never_caches_authority(secured, mode):
    manager, _, host, clock, _ = secured
    signer = CursorSigner(b"catalog-security-key-123456789012")
    catalog = ManagedCatalogService(manager, signer=signer, clock=lambda: clock[0])
    try:
        first = await catalog.start("g", ttl=5)
        if mode == "expiry":
            clock[0] = 5
        elif mode == "deny":
            host.denied = True
        elif mode == "rotation":
            signer.rotate(b"rotated-catalog-key-12345678901234")
        else:
            field = {"actor": "actor_id", "tenant": "tenant_id", "authority": "authority_revision"}[mode]
            host.context = replace(host.context, **{field: "2" if mode == "authority" else "other"})
        with pytest.raises(GraphError) as error:
            await catalog.continuation(first["streams"]["nodes"], graph_id="g", source="head", stream="nodes")
        assert error.value.code == "resync_required"
        assert catalog.counts == {"snapshots": 0, "pending": 0, "bytes": 0, "cursors": 0}
    finally:
        catalog.shutdown()


def test_management_history_checks_historical_scope_even_when_head_is_allowed(secured):
    manager, _, host, _, _ = secured
    commit(secured)
    original = host.decide

    def decide(req):
        if req.operation == "graph.read" and req.base_revision == "1":
            return AdmissionDecision(True, host.context.authority_revision, frozenset())
        return original(req)

    host.decide = decide
    service = ManagedManagementService(manager, mode="read_only")
    try:
        assert service.execute("head", {"graph_id": "g"})["result"]["revision"] == "2"
        refused(lambda: service.execute("history", {"graph_id": "g", "after": "1"}), "permission_denied")
        refused(lambda: service.execute("diff", {"graph_id": "g", "before": "1", "after": "2"}), "permission_denied")
    finally:
        service.close()


def test_cli_session_redacts_host_exception_and_does_not_parse_oversized_remainder(project):
    (project / "mode").write_text("deny")
    result = run(project, "session", raw=json.dumps({"command": "head", "arguments": {"graph_id": "g"}}) + "\n")
    assert result.returncode == 1 and not result.stderr
    failure = json.loads(result.stdout)
    assert failure["error"]["code"] == "permission_denied"
    assert "result" not in failure and "definition" not in result.stdout
    # Actual CLI line budget, not a lowered test-only approximation.
    raw = b" " * (16 * 1024 * 1024 + 1) + b'\n{"command":"head","arguments":{"graph_id":"g"}}\n'
    result = subprocess.run(command(project, "session"), input=raw, capture_output=True, timeout=30)
    assert result.returncode == 2 and not result.stderr
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout)["error"]["code"] == "limit_exceeded"
    assert SECRET.encode() not in result.stdout
