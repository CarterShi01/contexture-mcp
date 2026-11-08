"""Native paging through real compiler/FileJournal/owner/coordinator/runtime.

Authority is the existing explicit deployment-port fixture, not a replacement
for ManagedRuntime checks. No mocked graph, static snapshot, or execution path.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from typing import Any, cast

import pytest

from contexture.application.managed_paging import CursorSigner, ManagedPagingService, PageProfile
from contexture.application.managed_releases import (
    PROJECTION_NAME, ReleaseIntentCoordinator, release_projection,
)
from contexture.application.managed_run_leases import RunLeaseService
from contexture.core.graph.errors import GraphError
from contexture.core.graph.paging import slice_items
from contexture.server.managed_journal import FileJournal
from tests.test_managed_owner import LATER, SCHEMA, Host, owner, seed
from tests.test_managed_run_leases import ManualClock
from tests.test_managed_runtime import (
    Execution, Provider, binding_switch, change, definition, ref, setup, swap,
    system as system,
)


@pytest.fixture
def tree_system(tmp_path):
    with FileJournal.open(tmp_path / "journal", schema=SCHEMA) as journal:
        host, execution = Host(), Execution()
        provider = Provider(execution)
        manager = owner(journal, host, projections={PROJECTION_NAME: release_projection()})
        raw = cast(dict[str, Any], definition(provider).as_json())
        raw["nodes"].extend([
            {"node_id": "owner", "kind": "role", "name": "owner", "description": "Owner",
             "mutable": True, "instructions": "ORIGINAL PROGRAM", "uses": ["r", "w", "skill"],
             "members": {"pre_process": "pre", "children": ["branch"], "post_process": "post",
                         "skills": ["skill"], "tools": ["r", "w", "reserved"]}},
            {"node_id": "pre", "kind": "role", "name": "pre", "description": "Before",
             "role_variant": "pre_process", "instructions": "Prepare"},
            {"node_id": "post", "kind": "role", "name": "post", "description": "After",
             "role_variant": "post_process", "instructions": "Finish"},
            {"node_id": "branch", "kind": "role", "name": "branch", "description": "Branch",
             "instructions": "Branch"},
            {"node_id": "skill", "kind": "skill", "name": "skill", "description": "Skill",
             "instructions": "Skill"},
        ])
        raw["roots"] = [{"node_id": "owner", "plane": "model"},
                        {"node_id": "p", "plane": "prompt"}]
        envelope = seed()
        envelope["seed"]["inline"] = raw
        manager.commit_bootstrap(manager.preview_bootstrap(envelope, expires_at=LATER))
        yield manager, host, execution, provider, ReleaseIntentCoordinator(manager, manifests=provider)
        manager.close()


def services(runtime, *, clock=None, **options):
    runs = RunLeaseService(runtime, clock=clock)
    signer = CursorSigner(b"host-owned-test-key-" + b"x" * 32)
    return runs, signer, ManagedPagingService(runtime, runs=runs, signer=signer, clock=clock, **options)


def names(page, relation):
    return [card["name"] for card in page[relation + "_page"]["items"]]


def token(page, relation="routes"):
    return page[relation + "_page"]["next_cursor"]


def empty(pages, runs):
    assert pages.counts == {"snapshots": 0, "pending": 0, "bytes": 0, "cursors": 0}
    assert runs.counts == {"runs": 0, "pending": 0, "timers": 0}


@pytest.mark.asyncio
async def test_real_snapshot_swap_stable_order_detached_replay_and_resource_retirement(system):
    coordinator, authority, runtime = await setup(system)
    runs, _, pages = services(runtime)
    profile = PageProfile(limit=1)
    try:
        first = await pages.start("routes", profile=profile)
        assert names(first, "routes") == ["r"]
        sid = first["snapshot"]
        saved = pages._snapshots[sid]
        old = runs._validate(saved.run).lease.generation
        assert saved.revision == "1"
        assert saved.generation == old.context.generation_uuid
        assert not any(type(v).__name__ == "ManagedRequest" for v in vars(pages).values())
        binding_switch(system)
        await swap(system, coordinator)
        second = await pages.continuation(token(first), relation="routes", profile=profile)
        assert names(second, "routes") == ["w"] and second["snapshot"] == sid
        assert all(":" + saved.generation + ":" in x["ref"]
                   for x in second["routes_page"]["items"])
        assert second["routes_page"]["complete"]
        second["routes_page"]["items"][0]["description"] = "MUTATED"
        replay = await pages.continuation(token(first), relation="routes", profile=profile)
        assert replay["routes_page"]["items"][0]["description"] == "Business Tool"
        assert authority.calls[-1].current_index.definition.nodes["r"].binding.version == "B"
        assert not system[2].calls and not system[2].closed
        pages.shutdown()
        empty(pages, runs)
        await old.retire()
        assert len(system[2].closed) == 4
    finally:
        pages.shutdown()
        runs.shutdown()
        await coordinator.shutdown()
    assert len(system[2].closed) == 8


@pytest.mark.asyncio
async def test_role_open_processes_atomic_and_independent_relation_cursors(tree_system):
    coordinator, authority, runtime = await setup(tree_system)
    authority.scope = frozenset({"owner", "pre", "branch", "post", "skill", "r", "w", "reserved", "p"})
    runs, _, pages = services(runtime)
    profile = PageProfile(limit=1)
    try:
        first = await pages.start("open", reference=ref("owner"), profile=profile)
        assert names(first, "members") == ["pre"] and names(first, "uses") == ["r"]
        assert [x["designation"] for x in first["process_cards"]] == ["pre_process", "post_process"]
        active = first["owner_active"]
        assert active["pre_process"] == first["process_cards"][0]["card"]["ref"]
        assert active["post_process"] == first["process_cards"][1]["card"]["ref"]
        assert "ORIGINAL PROGRAM" in active["instructions"] and "does not execute" in active["instructions"]
        assert "reserved" not in json.dumps(first) and "count" not in json.dumps(first)
        expected = ["pre", "branch", "post", "skill", "r", "w"]
        members, current = names(first, "members"), first
        while not current["members_page"]["complete"]:
            current = await pages.continuation(token(current, "members"), relation="members",
                                             reference=ref("owner"), profile=profile)
            assert current["owner_active"] == active and current["process_cards"] == first["process_cards"]
            assert "uses_page" not in current
            members.extend(names(current, "members"))
        assert members == expected
        uses = await pages.continuation(token(first, "uses"), relation="uses",
                                        reference=ref("owner"), profile=profile)
        assert names(uses, "uses") == ["w"] and "members_page" not in uses
        assert await pages.continuation(token(first, "uses"), relation="uses",
                                       reference=ref("owner"), profile=profile) == uses
        assert not tree_system[2].calls
    finally:
        pages.shutdown()
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_relations_are_route_only_filter_is_bound_and_ordered(tree_system):
    coordinator, authority, runtime = await setup(tree_system)
    authority.scope = frozenset({"owner", "pre", "branch", "post", "skill", "r", "w"})
    runs, _, pages = services(runtime)
    profile = PageProfile(limit=1, kinds=frozenset({"tool"}), name="tools-only")
    try:
        for operation in ("members", "uses"):
            first = await pages.start(operation, reference=ref("owner"), profile=profile)
            assert names(first, operation) == ["r"]
            assert set(first[operation + "_page"]["items"][0]) == {"kind", "name", "description", "ref"}
            second = await pages.continuation(token(first, operation), relation=operation,
                                              reference=ref("owner"), profile=profile)
            assert names(second, operation) == ["w"] and second[operation + "_page"]["complete"]
    finally:
        pages.shutdown()
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["revoke", "deny", "delete", "unverifiable", "revision", "actor", "tenant"])
async def test_actual_current_authority_or_context_change_resync_and_clear(system, mode):
    coordinator, authority, runtime = await setup(system)
    runs, _, pages = services(runtime)
    profile = PageProfile(limit=1)
    try:
        first = await pages.start("routes", profile=profile)
        if mode == "revoke":
            authority.scope = frozenset({"r"})
        elif mode == "deny":
            authority.allowed = False
        elif mode == "delete":
            change(system, [{"op": "delete_nodes", "targets": ["r"], "deleted_ids": ["r"],
                "policy": "reject_references", "disconnections": [
                    {"source_id": "g", "target_id": "r", "relation": "containment"}]}])
            await swap(system, coordinator)
        elif mode == "unverifiable":
            authority.revision = "999"
        else:
            field = {"revision": "authority_revision", "actor": "actor_id", "tenant": "tenant_id"}[mode]
            system[1].context = replace(system[1].context, **{field: "2" if mode == "revision" else "other"})
        with pytest.raises(GraphError) as error:
            await pages.continuation(token(first), relation="routes", profile=profile)
        assert error.value.code == "resync_required"
        assert str(error.value) == str(GraphError("resync_required", "Native paging view is unavailable; resync required"))
        empty(pages, runs)
        assert not system[2].calls
    finally:
        pages.shutdown()
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["relation", "reference", "selection", "limit", "filter", "profile"])
async def test_exact_native_cursor_scope_profile_binding(system, mode):
    coordinator, _, runtime = await setup(system)
    runs, _, pages = services(runtime)
    profile = PageProfile(limit=1)
    try:
        first = await pages.start("routes", profile=profile)
        kwargs = {"relation": "routes", "profile": profile}
        if mode == "relation":
            kwargs["relation"] = "uses"
        elif mode == "reference":
            kwargs["reference"] = ref()
        elif mode == "selection":
            kwargs["selection"] = frozenset({"r"})
        else:
            kwargs["profile"] = replace(profile, **{
                "limit": {"limit": 2}, "filter": {"kinds": frozenset({"role"})},
                "profile": {"name": "other"}}[mode])
        with pytest.raises(GraphError) as error:
            await pages.continuation(token(first), **kwargs)
        assert error.value.code == "resync_required"
        empty(pages, runs)
    finally:
        pages.shutdown()
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_opaque_malformed_tampered_cross_store_and_rotated_tokens(system):
    coordinator, _, runtime = await setup(system)
    runs, signer, pages = services(runtime)
    other = ManagedPagingService(runtime, runs=runs, signer=signer)
    profile = PageProfile(limit=1)
    try:
        first = await pages.start("routes", profile=profile)
        handle = token(first)
        assert len(handle) == 129 and signer.valid(handle)
        assert all(x not in handle for x in ["tenant", "actor", "ctxg", "snapshot", "revision"])
        for bad in ("", "x" * 10000, "../etc", handle + "x", "0" * 64 + handle[64:],
                    handle[:65] + "0" * 64, None, {}, 2):
            with pytest.raises(GraphError) as error:
                await pages.continuation(cast(str, bad), relation="routes", profile=profile)
            assert error.value.code == "resync_required" and handle not in str(error.value)
        with pytest.raises(GraphError):
            await other.continuation(handle, relation="routes", profile=profile)
        assert pages.counts["snapshots"] == 1
        signer.rotate(b"new-host-key" + b"y" * 32)
        with pytest.raises(GraphError):
            await pages.continuation(handle, relation="routes", profile=profile)
        empty(pages, runs)
    finally:
        other.shutdown()
        pages.shutdown()
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("coordinate", ["graph_id", "target_id", "boot_id", "instance_id", "_store_origin"])
async def test_store_graph_target_boot_instance_are_bound(system, coordinate):
    coordinator, _, runtime = await setup(system)
    runs, _, pages = services(runtime)
    profile = PageProfile(limit=1)
    original = getattr(coordinator, coordinate)
    try:
        first = await pages.start("routes", profile=profile)
        setattr(coordinator, coordinate, ("other", "hash") if coordinate == "_store_origin" else "other")
        with pytest.raises(GraphError) as error:
            await pages.continuation(token(first), relation="routes", profile=profile)
        assert error.value.code == "resync_required"
        empty(pages, runs)
    finally:
        setattr(coordinator, coordinate, original)
        pages.shutdown()
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_ttl_cleanup_during_current_authority_await_and_no_resurrection(system):
    coordinator, authority, runtime = await setup(system)
    clock = ManualClock()
    runs, _, pages = services(runtime, clock=clock)
    profile = PageProfile(limit=1)
    try:
        assert pages.requires_explicit_sweep
        first = await pages.start("routes", profile=profile, ttl=5)
        authority.block_operation = "request"
        task = asyncio.create_task(pages.continuation(token(first), relation="routes", profile=profile))
        await authority.entered.wait()
        clock.now = 5
        assert pages.sweep() == 1
        authority.proceed.set()
        with pytest.raises(GraphError) as error:
            await task
        assert error.value.code == "resync_required"
        empty(pages, runs)
        assert pages.sweep() == 0
    finally:
        authority.proceed.set()
        pages.shutdown()
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_owned_timer_callback_expiry_without_next_request(system):
    coordinator, _, runtime = await setup(system)
    clock = ManualClock()
    callbacks = []

    class Timer:
        cancelled = False

        def cancel(self):
            self.cancelled = True

    def timer(delay, callback):
        handle = Timer()
        callbacks.append((handle, callback))
        return handle

    runs = RunLeaseService(runtime, clock=clock)
    pages = ManagedPagingService(runtime, runs=runs, signer=CursorSigner(b"x" * 32), clock=clock, timer=timer)
    try:
        assert not pages.requires_explicit_sweep
        await pages.start("routes", profile=PageProfile(limit=1), ttl=5)
        clock.now = 5
        handle, callback = callbacks[0]
        callback()
        empty(pages, runs)
        assert handle.cancelled
        callback()  # a late callback cannot resurrect anything
        empty(pages, runs)
    finally:
        pages.shutdown()
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_pending_capacity_and_cancelled_create_unwind(system):
    coordinator, authority, runtime = await setup(system)
    runs, _, pages = services(runtime, max_snapshots=1)
    try:
        authority.block_operation = "run.create"
        task = asyncio.create_task(pages.start("routes", profile=PageProfile(limit=1)))
        await authority.entered.wait()
        assert pages.counts["pending"] == 1
        with pytest.raises(GraphError) as error:
            await pages.start("routes")
        assert error.value.code == "resource_busy"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        empty(pages, runs)
        first = await pages.start("routes", profile=PageProfile(limit=1))
        with pytest.raises(GraphError):
            await pages.start("routes")
        assert first["snapshot"] in pages._snapshots
    finally:
        authority.proceed.set()
        pages.shutdown()
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", ["max_bytes", "max_items", "response_bytes", "max_cursors"])
async def test_material_response_single_item_and_handle_capacity_fail_without_leaks(tree_system, budget):
    coordinator, authority, runtime = await setup(tree_system)
    authority.scope = frozenset({"owner", "pre", "branch", "post", "skill", "r", "w"})
    runs, _, pages = services(runtime, **{budget: 1 if budget != "response_bytes" else 200})
    try:
        with pytest.raises(GraphError) as error:
            await pages.start("open", reference=ref("owner"), profile=PageProfile(limit=1))
        assert error.value.code in ("too_large", "resource_busy")
        empty(pages, runs)
        assert not tree_system[2].calls
    finally:
        pages.shutdown()
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_unauthorized_process_fails_whole_open_without_hidden_counts(tree_system):
    coordinator, authority, runtime = await setup(tree_system)
    authority.scope = frozenset({"owner", "branch", "skill", "r", "w"})
    runs, _, pages = services(runtime)
    try:
        with pytest.raises(GraphError) as error:
            await pages.start("open", reference=ref("owner"), profile=PageProfile(limit=1))
        assert error.value.code == "permission_denied"
        assert not any(x in str(error.value) for x in ["pre", "post", "owner", "count"])
        empty(pages, runs)
    finally:
        pages.shutdown()
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_fixed_selection_does_not_expand_on_new_permission(system):
    coordinator, authority, runtime = await setup(system)
    runs, _, pages = services(runtime)
    try:
        authority.scope = frozenset({"r"})
        first = await pages.start("routes", selection=frozenset({"r", "w"}))
        assert names(first, "routes") == ["r"] and first["routes_page"]["complete"]
        authority.scope = frozenset({"r", "w"})
        assert pages._snapshots[first["snapshot"]].ceiling == frozenset({"r"})
        selected = await pages.start("routes", selection=frozenset())
        assert selected["routes_page"] == {"items": [], "complete": True}
        assert "count" not in json.dumps(first)
    finally:
        pages.shutdown()
        runs.shutdown()
        await coordinator.shutdown()


def test_pure_immutable_slicing_exact_boundaries_and_no_large_single_truncation():
    data = (b"{}", b'{"x":1}', b"[]")
    page = slice_items(data, offset=0, limit=3, byte_limit=4)
    assert page.items == (b"{}",) and page.next_offset == 1
    with pytest.raises(GraphError) as error:
        slice_items(data, offset=1, limit=2, byte_limit=4)
    assert error.value.code == "too_large"
    assert slice_items(data, offset=3, limit=1, byte_limit=2).next_offset is None
    assert data == (b"{}", b'{"x":1}', b"[]")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["revoke", "cancel", "shutdown"])
async def test_continuation_policy_await_unwinds_owned_resources(system, mode):
    coordinator, authority, runtime = await setup(system)
    runs, _, pages = services(runtime)
    profile = PageProfile(limit=1)
    task = None
    try:
        first = await pages.start("routes", profile=profile)
        authority.block_operation = "open"
        task = asyncio.create_task(pages.continuation(token(first), relation="routes", profile=profile))
        await authority.entered.wait()
        if mode == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            if mode == "revoke":
                authority.scope = frozenset()
            else:
                pages.shutdown()
            authority.proceed.set()
            with pytest.raises(GraphError) as error:
                await task
            assert error.value.code == "resync_required"
        empty(pages, runs)
        assert not system[2].calls
    finally:
        authority.proceed.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        pages.shutdown()
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_global_retained_byte_quota_is_not_per_snapshot(system):
    coordinator, _, runtime = await setup(system)
    runs, _, pages = services(runtime)
    try:
        first = await pages.start("routes")
        size = pages.counts["bytes"]
        assert size > 1
        pages.max_bytes = size
        with pytest.raises(GraphError) as error:
            await pages.start("routes")
        assert error.value.code == "resource_busy"
        assert pages.counts["bytes"] == size and pages.counts["snapshots"] == 1
        assert runs.counts["runs"] == 1 and first["snapshot"] in pages._snapshots
    finally:
        pages.shutdown()
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_independent_real_filejournal_store_rejects_foreign_cursor(system, tree_system):
    c1, _, runtime1 = await setup(system)
    c2, _, runtime2 = await setup(tree_system)
    runs1, signer, pages1 = services(runtime1)
    runs2 = RunLeaseService(runtime2)
    pages2 = ManagedPagingService(runtime2, runs=runs2, signer=signer)
    profile = PageProfile(limit=1)
    try:
        assert c1._store_origin != c2._store_origin
        first = await pages1.start("routes", profile=profile)
        with pytest.raises(GraphError) as error:
            await pages2.continuation(token(first), relation="routes", profile=profile)
        assert error.value.code == "resync_required"
        empty(pages2, runs2)
        assert pages1.counts["snapshots"] == 1
    finally:
        pages1.shutdown()
        pages2.shutdown()
        runs1.shutdown()
        runs2.shutdown()
        await c1.shutdown()
        await c2.shutdown()