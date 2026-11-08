"""Catalog evidence using real FileJournal, owner, compiler and serving runtime."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json

import pytest

from contexture.application.managed_catalog import CatalogProfile, ManagedCatalogService
from contexture.application.managed_paging import CursorSigner
from contexture.core.graph.canonical import canonical_json
from contexture.core.graph.errors import GraphError
from contexture.core.graph.history import History
from contexture.core.graph.policy import AdmissionDecision
from tests.test_managed_paging import tree_system as tree_system
from tests.test_managed_run_leases import ManualClock
from tests.test_managed_runtime import binding_switch, change, setup, swap, system as system


def service(system, runtime=None, **options):
    manager, host = system[:2]
    original = host.decide
    host.catalog_scope = frozenset({"owner", "pre", "post", "branch", "skill", "r", "w", "p", "reserved"})

    def decide(request):
        if request.operation == "graph.catalog":
            assert host.fences > 0
            host.calls.append(request)
            return AdmissionDecision(not host.denied, host.context.authority_revision, host.catalog_scope)
        return original(request)

    host.decide = decide
    return ManagedCatalogService(manager, runtime=runtime, signer=CursorSigner(b"catalog-host-key-" + b"x" * 32), **options)


async def read(catalog, first, stream="nodes", **kwargs):
    result = []
    token = first["streams"][stream]
    while True:
        page = await catalog.continuation(token, graph_id="g", source=first["source"], stream=stream, **kwargs)
        result.extend(page["items"])
        if page["complete"]:
            return result
        token = page["next_cursor"]


@pytest.mark.asyncio
async def test_head_full_nodes_relations_independent_streams_retry_detached(tree_system):
    catalog = service(tree_system)
    profile = CatalogProfile(limit=1)
    try:
        first = await catalog.start("g", profile=profile)
        nodes = await read(catalog, first, profile=profile)
        assert [n["node_id"] for n in nodes] == ["owner", "pre", "branch", "post", "skill", "r", "w", "reserved", "p"]
        edges = await read(catalog, first, "containment", profile=profile)
        assert len(edges) == 7
        assert await read(catalog, first, "uses", profile=profile) == [
            {"source_id": "owner", "target_id": n} for n in ["r", "w", "skill"]]
        token = first["streams"]["nodes"]
        a = await catalog.continuation(token, graph_id="g", source="head", stream="nodes", profile=profile)
        b = await catalog.continuation(token, graph_id="g", source="head", stream="nodes", profile=profile)
        assert a == b
        a["items"][0]["name"] = "mutated"
        assert b["items"][0]["name"] == "owner"
        text = json.dumps([first, nodes, edges])
        for forbidden in ["ORIGINAL PROGRAM", "input_schema", "definition_digest", "ordinal", "count", "binding_id"]:
            assert forbidden not in text
        assert not tree_system[2].calls
    finally:
        catalog.shutdown()


@pytest.mark.asyncio
async def test_scope_projection_hidden_digest_selection_and_filter(tree_system):
    catalog = service(tree_system)
    host = tree_system[1]
    host.catalog_scope = frozenset({"r", "w"})
    try:
        first = await catalog.start("g")
        assert all(n["projected_root"] for n in await read(catalog, first))
        assert not await read(catalog, first, "containment")
        change(tree_system, [{"op": "rename_node", "node_id": "owner", "new_name": "secret-change"}])
        second = await catalog.start("g")
        assert second["revision"] != first["revision"]
        assert second["view_digest"] == first["view_digest"]
        assert await read(catalog, first) == await read(catalog, second)
        empty = await catalog.start("g", selection=frozenset())
        assert not await read(catalog, empty, selection=frozenset())
        selected = await catalog.start("g", selection=frozenset({"r"}))
        assert [n["node_id"] for n in await read(catalog, selected, selection=frozenset({"r"}))] == ["r"]
        filtered = CatalogProfile(kinds=frozenset({"role"}))
        assert not await read(catalog, await catalog.start("g", profile=filtered), profile=filtered)
    finally:
        catalog.shutdown()


@pytest.mark.asyncio
async def test_revision_is_actual_history_intersected_with_current_authorized_ids(system):
    catalog = service(system)
    try:
        change(system, [{"op": "rename_node", "node_id": "r", "new_name": "current-name"}])
        old = await catalog.start("g", source="revision", revision="1")
        head = await catalog.start("g")
        assert (await read(catalog, old, revision="1"))[0]["name"] == "r"
        assert (await read(catalog, head))[0]["name"] == "current-name"
        system[1].catalog_scope = frozenset({"w"})
        with pytest.raises(GraphError, match="resync required"):
            await read(catalog, old, revision="1")
        narrowed = await catalog.start("g", source="revision", revision="1")
        assert [n["node_id"] for n in await read(catalog, narrowed, revision="1")] == ["w"]
    finally:
        catalog.shutdown()


@pytest.mark.asyncio
async def test_serving_retains_real_generation_current_auth_and_no_execution(system):
    coordinator, authority, runtime = await setup(system)
    catalog = service(system, runtime)
    try:
        first = await catalog.start("g", source="serving")
        assert first["revision"] == "1" and first["target_id"] == "t"
        assert first["generation_uuid"].endswith("0065")
        assert [n["node_id"] for n in await read(catalog, first)] == ["r", "w", "reserved"]
        binding_switch(system)
        await swap(system, coordinator)
        assert not system[2].closed
        assert await read(catalog, first)
        assert authority.calls[-1].current_index.definition.nodes["r"].binding.version == "B"
        new = await catalog.start("g", source="serving")
        assert new["generation_uuid"] != first["generation_uuid"]
        authority.scope = frozenset({"w"})
        with pytest.raises(GraphError, match="resync required"):
            await read(catalog, first)
        assert not system[2].calls
    finally:
        catalog.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["actor_id", "tenant_id", "authority_revision", "scope", "deny", "origin", "profile", "stream", "source", "selection"])
async def test_revocation_exact_bindings_clear_entire_snapshot(system, mode):
    catalog = service(system)
    try:
        first = await catalog.start("g")
        kwargs = dict(graph_id="g", source="head", stream="nodes")
        host = system[1]
        if mode in ("actor_id", "tenant_id", "authority_revision"):
            host.context = replace(host.context, **{mode: "2" if mode == "authority_revision" else "other"})
        elif mode == "scope":
            host.catalog_scope = frozenset()
        elif mode == "deny":
            host.denied = True
        elif mode == "origin":
            catalog._snapshots[first["snapshot"]].coordinates = ("other", "g")
        elif mode == "profile":
            kwargs[mode] = CatalogProfile(limit=1)
        elif mode == "selection":
            kwargs[mode] = frozenset({"r"})
        else:
            kwargs[mode] = "uses" if mode == "stream" else "revision"
        with pytest.raises(GraphError) as error:
            await catalog.continuation(first["streams"]["nodes"], **kwargs)
        assert error.value.code == "resync_required"
        assert catalog.counts == {"snapshots": 0, "pending": 0, "bytes": 0, "cursors": 0}
    finally:
        catalog.shutdown()


@pytest.mark.asyncio
async def test_opaque_retry_expiry_rotation_and_cross_service(system):
    clock = ManualClock()
    catalog = service(system, clock=clock)
    other = ManagedCatalogService(system[0], signer=catalog._signer, clock=clock)
    try:
        first = await catalog.start("g", ttl=5)
        token = first["streams"]["nodes"]
        assert len(token) == 129
        for bad in ["", token + "x", "x" * 10000]:
            with pytest.raises(GraphError):
                await catalog.continuation(bad, graph_id="g", source="head", stream="nodes")
        with pytest.raises(GraphError):
            await read(other, first)
        assert await read(catalog, first)
        clock.now = 5
        assert catalog.sweep() == 1
        with pytest.raises(GraphError):
            await read(catalog, first)
        second = await catalog.start("g")
        catalog._signer.rotate(b"y" * 32)
        with pytest.raises(GraphError):
            await read(catalog, second)
        assert not catalog.counts["snapshots"]
    finally:
        other.shutdown()
        catalog.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", ["max_items", "max_bytes", "max_cursors", "response_bytes"])
async def test_budget_refusal_no_partial_snapshot_or_leak(system, budget):
    catalog = service(system, **{budget: 1})
    try:
        with pytest.raises(GraphError) as error:
            await catalog.start("g")
        assert error.value.code in ("too_large", "resource_busy")
        assert catalog.counts == {"snapshots": 0, "pending": 0, "bytes": 0, "cursors": 0}
    finally:
        catalog.shutdown()


@pytest.mark.asyncio
async def test_byte_paging_complete_envelope_is_bounded(tree_system):
    catalog = service(tree_system, response_bytes=1100)
    profile = CatalogProfile(limit=1000)
    try:
        first = await catalog.start("g", profile=profile)
        token = first["streams"]["nodes"]
        found = []
        while True:
            page = await catalog.continuation(token, graph_id="g", source="head", stream="nodes", profile=profile)
            assert len(canonical_json(page)) <= 1100
            found.extend(n["node_id"] for n in page["items"])
            if page["complete"]:
                break
            token = page["next_cursor"]
        assert len(found) == 9 and len(set(found)) == 9
    finally:
        catalog.shutdown()


@pytest.mark.asyncio
async def test_serving_expiry_during_authority_await_and_pending_cancel(system):
    coordinator, authority, runtime = await setup(system)
    clock = ManualClock()
    catalog = service(system, runtime, clock=clock, max_snapshots=1)
    try:
        authority.block_operation = "route"
        task = asyncio.create_task(catalog.start("g", source="serving", ttl=5))
        await authority.entered.wait()
        with pytest.raises(GraphError, match="capacity unavailable"):
            await catalog.start("g")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not catalog.counts["snapshots"] and not catalog.counts["pending"]
        first = await catalog.start("g", source="serving", ttl=5)
        authority.entered.clear()
        authority.block_operation = "route"
        task = asyncio.create_task(read(catalog, first))
        await authority.entered.wait()
        clock.now = 5
        assert catalog.sweep() == 1
        authority.proceed.set()
        with pytest.raises(GraphError, match="resync required"):
            await task
        assert not catalog.counts["snapshots"]
    finally:
        authority.proceed.set()
        catalog.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_history_current_deleted_id_never_reappears_and_retention_is_owned(system):
    catalog = service(system)
    try:
        retained = await catalog.start("g", source="revision", revision="1")
        history = system[0]._histories["g"]
        # Snapshot byte lifetime is independent of later owner history pruning.
        change(system, [{"op": "rename_node", "node_id": "w", "new_name": "new-w"}])
        system[0]._histories["g"] = History.seed(system[0]._histories["g"].head, journal_seq="2")
        assert (await read(catalog, retained, revision="1"))[1]["name"] == "w"
        # The in-memory window is not retention: older revisions are rebuilt
        # from the journal on demand, never served from a stale cache.
        rebuilt = await catalog.start("g", source="revision", revision="1")
        assert (await read(catalog, rebuilt, revision="1"))[1]["name"] == "w"
        system[0]._histories["g"] = history
    finally:
        catalog.shutdown()


@pytest.mark.asyncio
async def test_revision_current_deleted_node_is_excluded(system):
    catalog = service(system)
    try:
        change(system, [{"op": "delete_nodes", "targets": ["r"], "deleted_ids": ["r"],
                         "policy": "reject_references", "disconnections": [
                             {"source_id": "g", "target_id": "r", "relation": "containment"}]}])
        first = await catalog.start("g", source="revision", revision="1")
        assert "r" not in [n["node_id"] for n in await read(catalog, first, revision="1")]
    finally:
        catalog.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("coordinate", ["graph_id", "target_id", "instance_id", "boot_id", "_store_origin"])
async def test_serving_current_coordinates_bind_every_stream(system, coordinate):
    coordinator, _, runtime = await setup(system)
    catalog = service(system, runtime)
    original = getattr(coordinator, coordinate)
    try:
        first = await catalog.start("g", source="serving")
        setattr(coordinator, coordinate, ("other", "hash") if coordinate == "_store_origin" else "other")
        with pytest.raises(GraphError) as error:
            await read(catalog, first)
        assert error.value.code == "resync_required"
        assert not catalog.counts["snapshots"]
    finally:
        setattr(coordinator, coordinate, original)
        catalog.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_expiry_timer_and_shutdown_cleanup_without_another_page(system):
    clock = ManualClock()
    callbacks = []

    class Handle:
        cancelled = False

        def cancel(self):
            self.cancelled = True

    def timer(delay, callback):
        handle = Handle()
        callbacks.append((handle, callback))
        return handle

    catalog = service(system, clock=clock, timer=timer)
    try:
        assert not catalog.requires_explicit_sweep
        await catalog.start("g", ttl=5)
        clock.now = 5
        handle, callback = callbacks[0]
        callback()
        assert handle.cancelled and not catalog.counts["snapshots"]
        callback()
        assert not catalog.counts["cursors"]
    finally:
        catalog.shutdown()


@pytest.mark.asyncio
async def test_atomic_oversized_node_refuses_even_if_a_later_page_would_hold_it(system):
    change(system, [{"op": "rename_node", "node_id": "w", "new_name": "w" * 2000}])
    catalog = service(system, response_bytes=1100)
    try:
        with pytest.raises(GraphError) as error:
            await catalog.start("g", profile=CatalogProfile(limit=1))
        assert error.value.code == "too_large"
        assert not catalog.counts["snapshots"]
    finally:
        catalog.shutdown()