"""Local RunLease evidence through real GraphCompiler/FileJournal/serving."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from contexture.application.managed_generation import RetainedGeneration
from contexture.application.managed_run_leases import RunLeaseContext, RunLeaseService
from contexture.application.managed_runtime import ManagedRuntime
from contexture.application.managed_serving import LocalServingCoordinator
from contexture.core.graph.bindings import RegistryGeneration
from contexture.core.graph.errors import GraphError
from tests.test_managed_runtime import (
    Authority, binding_switch, change, json_output, ref, setup, swap,
    system as system,
)
from tests.test_managed_serving import uid


class ManualClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def handlers(system):
    return [entry for entry in system[2].calls if entry[2] == "handler"]


@pytest.mark.asyncio
async def test_old_run_new_request_after_swap_and_ordinary_stale(system):
    coordinator, authority, runtime = await setup(system)
    runs = RunLeaseService(runtime)
    try:
        run = await runs.create(ttl=30, selection=frozenset({"r"}))
        binding_switch(system)
        await swap(system, coordinator)
        async with runtime.request() as request:
            with pytest.raises(GraphError) as error:
                await request.invoke_read_only(ref())
            assert error.value.code == "stale_reference"
        async with runtime.request(run_context=run) as request:
            assert request.generation_uuid == uid(101)
            assert json_output(await request.invoke_read_only(ref(), {"count": "6"}))["version"] == "A"
            with pytest.raises(GraphError):
                await request.invoke(ref("w"))
            with pytest.raises(GraphError) as error:
                await request.invoke_read_only(ref(generation=102))
            assert error.value.code == "stale_reference"
        assert authority.calls[-1].current_index.definition.nodes["r"].binding.version == "B"
        assert ("A", "r") not in system[2].closed
        runs.release(run)
        with pytest.raises(GraphError):
            async with runtime.request(run_context=run):
                pytest.fail("Released run admitted")
        assert handlers(system) == [("A", "r", "handler")]
    finally:
        runs.shutdown()
        await coordinator.shutdown()
    assert runs.counts == {"runs": 0, "pending": 0, "timers": 0}
    assert coordinator.counts == {"preparing": 0, "retiring": 0, "cleanup_errors": 0, "active": 0}
    assert len(system[2].closed) == 8


@pytest.mark.asyncio
async def test_renew_ttl_only_revision_changes_and_upgrade_fresh_ceiling(system):
    coordinator, authority, runtime = await setup(system)
    clock = ManualClock()
    runs = RunLeaseService(runtime, clock=clock)
    try:
        authority.scope = frozenset({"r"})
        run = await runs.create(ttl=5)
        entry = runs._validate(run)
        assert entry.ceiling == frozenset({"r"}) and entry.authority_view == "1"
        authority.scope = frozenset({"r", "w"})
        system[1].context = replace(system[1].context, authority_revision="2")
        clock.now = 4
        assert await runs.renew(run, ttl=20) is run
        assert entry.expires == 24 and entry.ceiling == frozenset({"r"})
        clock.now = 6
        async with runtime.request(run_context=run) as request:
            with pytest.raises(GraphError):
                await request.invoke(ref("w"))
            await request.invoke_read_only(ref())
        binding_switch(system)
        await swap(system, coordinator)
        upgraded = await runs.upgrade(run, ttl=10)
        assert upgraded is not run and upgraded._token != run._token
        new = runs._validate(upgraded)
        assert new.authority_view == "2" and new.ceiling == frozenset({"r", "w"})
        assert new.lease.generation.plan.manifest != entry.lease.generation.plan.manifest
        assert new.lease.generation.context.generation_uuid == uid(102)
        assert authority.calls[-1].operation == "run.create"
        with pytest.raises(GraphError):
            runs._validate(run)
        async with runtime.request(run_context=upgraded) as request:
            assert json_output(await request.invoke_read_only(ref(generation=102), {"count": 1}))["version"] == "B"
            await request.invoke(ref("w", generation=102))
    finally:
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_upgrade_current_denial_or_approval_never_reuses_old_approval(system):
    coordinator, authority, runtime = await setup(system)
    runs = RunLeaseService(runtime)
    try:
        run = await runs.create(ttl=30)
        original = authority.verify

        async def requires_approval(request):
            return replace(await original(request), approval_required=True)

        authority.verify = requires_approval
        with pytest.raises(GraphError):
            await runs.upgrade(run, ttl=30)
        assert runs.counts["runs"] == 1 and runs._validate(run)
        authority.verify = original
        authority.allowed = False
        with pytest.raises(GraphError):
            await runs.upgrade(run, ttl=30)
        assert runs.counts["runs"] == 1
    finally:
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["actor_id", "tenant_id", "boot_id", "target_id", "graph_id"])
async def test_bound_contexts_and_untrusted_envelopes_deny_before_handler(system, field):
    coordinator, _, runtime = await setup(system)
    runs = RunLeaseService(runtime)
    try:
        run = await runs.create(ttl=30)
        if field in ("actor_id", "tenant_id"):
            old = system[1].context
            system[1].context = replace(old, **{field: "other"})
        else:
            old = getattr(coordinator, field)
            setattr(coordinator, field, "other")
        try:
            with pytest.raises(GraphError) as error:
                async with runtime.request(run_context=run):
                    pytest.fail("Mismatched context admitted")
            assert run._token not in str(error.value) and run._token not in repr(run)
            with pytest.raises(GraphError):
                await runs.renew(run, ttl=30)
            with pytest.raises(GraphError):
                runs.release(run)
        finally:
            if field in ("actor_id", "tenant_id"):
                system[1].context = old
            else:
                setattr(coordinator, field, old)
        assert not handlers(system)
    finally:
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_real_new_boot_and_other_service_reject_old_token(system):
    coordinator, _, runtime = await setup(system)
    runs = RunLeaseService(runtime)
    run = await runs.create(ttl=30)
    runs.shutdown()
    await coordinator.shutdown()
    fresh = LocalServingCoordinator(system[0], graph_id="g", target_id="t", instance_id="local",
        boot_id=uid(999), provider=system[3])
    assert (await fresh.prepare(attempt_id=uid(3), generation_id=uid(103))).status == "local_observed"
    native = ManagedRuntime(fresh, contexts=system[1], authority=Authority(system[1]),
                            adapters=system[3].execution_adapters())
    new_runs = RunLeaseService(native)
    try:
        for envelope in (run, RunLeaseContext(new_runs, run._token)):
            with pytest.raises(GraphError):
                async with native.request(run_context=envelope):
                    pytest.fail("Old boot admitted")
        assert not handlers(system)
    finally:
        new_runs.shutdown()
        await fresh.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["revoke", "delete", "unverifiable"])
async def test_run_current_authority_rechecks_even_old_pin(system, mode):
    coordinator, authority, runtime = await setup(system)
    runs = RunLeaseService(runtime)
    try:
        run = await runs.create(ttl=30)
        async with runtime.request(run_context=run) as request:
            if mode == "delete":
                change(system, [{"op": "delete_nodes", "targets": ["r"], "deleted_ids": ["r"],
                    "policy": "reject_references", "disconnections": [
                        {"source_id": "g", "target_id": "r", "relation": "containment"}]}])
                await swap(system, coordinator)
                assert "r" in authority.scope  # lying authority cannot restore deleted stable IDs
            elif mode == "revoke":
                authority.scope = frozenset()
            else:
                authority.revision = "999"
            with pytest.raises(GraphError):
                await request.invoke_read_only(ref())
        assert not handlers(system)
    finally:
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_creation_pins_before_first_authority_await_and_cancellation_unwinds(system):
    coordinator, authority, runtime = await setup(system)
    runs = RunLeaseService(runtime)
    try:
        authority.block_operation = "run.create"
        task = asyncio.create_task(runs.create(ttl=30))
        await authority.entered.wait()
        binding_switch(system)
        await swap(system, coordinator)
        authority.proceed.set()
        run = await task
        async with runtime.request(run_context=run) as request:
            assert request.generation_uuid == uid(101)
            await request.invoke_read_only(ref())
        # A NEW run after the swap cannot pin draining A.
        current = await runs.create(ttl=30)
        assert runs._validate(current).lease.generation.context.generation_uuid == uid(102)
        authority.entered.clear()
        authority.proceed.clear()
        authority.block_operation = "run.create"
        task = asyncio.create_task(runs.create(ttl=30))
        await authority.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runs.counts["pending"] == 0 and runs.counts["runs"] == 2
    finally:
        runs.shutdown()
        await coordinator.shutdown()
    assert len(system[2].closed) == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("termination", ["expiry", "release", "cancel"])
async def test_admitted_handler_retains_resources_until_real_finish(system, termination):
    entered, finish = asyncio.Event(), asyncio.Event()
    provider = system[3]
    original = next(r for r in provider.registrations if r.binding_id == "r" and r.version == "A")

    async def blocked_handler(decoded, context, resource):
        entered.set()
        await finish.wait()
        return await original.handler(decoded, context, resource)

    provider.registrations = [replace(r, handler=blocked_handler) if r is original else r
                              for r in provider.registrations]
    provider.registry = RegistryGeneration(build="blocking", registrations=provider.registrations)
    coordinator, _, runtime = await setup(system)
    clock = ManualClock()
    runs = RunLeaseService(runtime, clock=clock)
    run = await runs.create(ttl=5)
    old = runs._validate(run).lease.generation

    async def invoke():
        async with runtime.request(run_context=run) as request:
            result = await request.invoke_read_only(ref())
            with pytest.raises(GraphError):
                await request.invoke_read_only(ref())
            return result

    task = asyncio.create_task(invoke())
    try:
        await entered.wait()
        binding_switch(system)
        await swap(system, coordinator)
        # A barrier starts the coordinator-owned retirement; no sleep/deadline.
        barrier = asyncio.Event()
        asyncio.get_running_loop().call_soon(barrier.set)
        await barrier.wait()
        assert old.resource_state == "draining"
        with pytest.raises(GraphError):
            old.retain()
        with pytest.raises(GraphError):
            async with old.lease():
                pytest.fail("Unretained draining lease admitted")
        if termination == "expiry":
            clock.now = 5
            assert runs.sweep() == 1
        else:
            runs.release(run)
        assert not system[2].closed and old.resource_state == "draining"
        assert runs.counts["runs"] == 0
        with pytest.raises(GraphError):
            async with runtime.request(run_context=run):
                pytest.fail("Terminated run admitted")
        if termination == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            finish.set()
            assert json_output(await task)["version"] == "A"
        await old.retire()
        assert old.resource_state == "retired" and len(system[2].closed) == 4
    finally:
        finish.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        runs.shutdown()
        await coordinator.shutdown()
    assert len(system[2].closed) == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["request", "invoke_read_only", "run.renew"])
async def test_expiry_during_authority_await_refuses_new_dispatch_or_resurrection(system, operation):
    coordinator, authority, runtime = await setup(system)
    clock = ManualClock()
    runs = RunLeaseService(runtime, clock=clock)
    try:
        run = await runs.create(ttl=5)
        authority.block_operation = operation

        async def expire():
            await authority.entered.wait()
            clock.now = 5
            assert runs.sweep() == 1
            authority.proceed.set()

        task = asyncio.create_task(expire())
        if operation == "request":
            with pytest.raises(GraphError):
                async with runtime.request(run_context=run):
                    pytest.fail("Expired request admitted")
        elif operation == "run.renew":
            with pytest.raises(GraphError):
                await runs.renew(run, ttl=20)
        else:
            async with runtime.request(run_context=run) as request:
                with pytest.raises(GraphError):
                    await request.invoke_read_only(ref())
        await task
        assert not handlers(system) and runs.counts["runs"] == 0
    finally:
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_timer_expiry_and_manual_clock_explicit_sweep_contract(system):
    coordinator, _, runtime = await setup(system)
    runs = RunLeaseService(runtime)
    manual = RunLeaseService(runtime, clock=ManualClock())
    try:
        assert not runs.requires_explicit_sweep and manual.requires_explicit_sweep
        run = await runs.create(ttl=0.001)
        expired = asyncio.Event()
        timer = asyncio.get_running_loop().call_later(0.002, expired.set)
        try:
            await expired.wait()
        finally:
            timer.cancel()
        assert runs.counts == {"runs": 0, "pending": 0, "timers": 0}
        with pytest.raises(GraphError):
            async with runtime.request(run_context=run):
                pytest.fail("Timer-expired run admitted")
        manual_run = await manual.create(ttl=5)
        assert manual.counts["timers"] == 0
        assert manual.sweep() == 0
        manual.release(manual_run)
    finally:
        manual.shutdown()
        runs.shutdown()
        await coordinator.shutdown()


class ManualTimer:
    def __init__(self):
        self.handles = []

    def __call__(self, delay, callback):
        class Handle:
            cancelled = False

            def cancel(self):
                self.cancelled = True

        handle = Handle()
        self.handles.append((delay, callback, handle))
        return handle


@pytest.mark.asyncio
async def test_injected_timer_renew_cancels_old_handle_and_expiry_cleans_mapping(system):
    coordinator, _, runtime = await setup(system)
    clock, timer = ManualClock(), ManualTimer()
    runs = RunLeaseService(runtime, clock=clock, timer=timer)
    try:
        run = await runs.create(ttl=5)
        assert not runs.requires_explicit_sweep
        first = timer.handles[-1]
        clock.now = 3
        await runs.renew(run, ttl=10)
        assert first[2].cancelled
        clock.now = 6
        first[1]()  # even a stale trusted callback must not expire renewed lease
        assert runs.counts["runs"] == 1
        clock.now = 13
        timer.handles[-1][1]()
        assert runs.counts == {"runs": 0, "pending": 0, "timers": 0}
        with pytest.raises(GraphError):
            await runs.renew(run, ttl=5)
    finally:
        runs.shutdown()
        await coordinator.shutdown()
    assert all(handle.cancelled for _, _, handle in timer.handles)


@pytest.mark.asyncio
async def test_capacity_reserves_pending_creates_and_reclaims_expiration(system):
    coordinator, authority, runtime = await setup(system)
    clock = ManualClock()
    runs = RunLeaseService(runtime, max_runs=1, clock=clock)
    try:
        authority.block_operation = "run.create"
        task = asyncio.create_task(runs.create(ttl=5))
        await authority.entered.wait()
        with pytest.raises(GraphError) as error:
            await runs.create(ttl=5)
        assert error.value.code == "resource_busy" and runs.counts["pending"] == 1
        authority.proceed.set()
        run = await task
        with pytest.raises(GraphError):
            await runs.upgrade(run, ttl=5)
        assert runs._validate(run)
        clock.now = 5
        replacement = await runs.create(ttl=5)  # explicit create sweeps expired slots
        assert runs.counts["runs"] == 1 and replacement._token != run._token
        with pytest.raises(GraphError):
            runs._validate(run)
        runs.release(replacement)
        assert runs.counts["runs"] == 0
    finally:
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_native_token_schema_entropy_unknown_service_and_no_public_draining_flag(system):
    coordinator, _, runtime = await setup(system)
    runs = RunLeaseService(runtime, max_runs=32)
    other = RunLeaseService(runtime)
    try:
        envelopes = [await runs.create(ttl=30) for _ in range(24)]
        tokens = [envelope._token for envelope in envelopes]
        assert len(set(tokens)) == 24 and all(len(token) == 43 for token in tokens)
        for token in ("", "x" * 42, "x" * 44, "!" * 43, "A" * 42 + "B", 123, {}, None):
            with pytest.raises(GraphError) as error:
                RunLeaseContext(runs, token)
            assert "Run lease" in str(error.value)
        for envelope in (RunLeaseContext(runs, "A" * 43), RunLeaseContext(other, tokens[0]), {}):
            with pytest.raises(GraphError):
                async with runtime.request(run_context=envelope):
                    pytest.fail("Unissued envelope admitted")
        with pytest.raises(TypeError):
            RetainedGeneration()
        async with coordinator.request_lease() as lease:
            with pytest.raises(TypeError):
                lease.generation.lease(allow_draining=True)
        for ttl in (0, -1, float("nan"), float("inf"), True, 301, "10"):
            with pytest.raises(GraphError):
                await runs.create(ttl=ttl)
        assert not handlers(system)
    finally:
        other.shutdown()
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_loop_affinity_without_cross_loop_resource_mutation(system):
    coordinator, _, runtime = await setup(system)
    runs = RunLeaseService(runtime)
    try:
        run = await runs.create(ttl=30)

        async def wrong_loop():
            for action in (runs.sweep, lambda: runs.release(run), runs.shutdown):
                with pytest.raises(GraphError):
                    action()
            with pytest.raises(GraphError):
                await runs.create(ttl=30)
            with pytest.raises(GraphError):
                async with runtime.request(run_context=run):
                    pytest.fail("Cross-loop run admitted")

        await asyncio.to_thread(lambda: asyncio.run(wrong_loop()))
        assert runs.counts["runs"] == 1
    finally:
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_stable_id_selection_survives_path_rename_not_path_alias(system):
    coordinator, authority, runtime = await setup(system)
    runs = RunLeaseService(runtime)
    try:
        with pytest.raises(GraphError):
            await runs.create(ttl=30, selection=frozenset({"/r"}))
        run = await runs.create(ttl=30, selection=frozenset({"r"}))
        binding = system[0].read("g").definition.nodes["r"].as_json()["binding"]
        change(system, [{"op": "rename_node", "node_id": "r", "new_name": "renamed"},
            {"op": "update_node", "node_id": "r", "set_fields": {
                "binding": {**binding, "version": "B"}}, "unset_fields": []}])
        await swap(system, coordinator)
        async with runtime.request(run_context=run, selection=frozenset({"r"})) as request:
            await request.invoke_read_only(ref())
        assert authority.calls[-1].current_index.path("r") == "/renamed"
        async with runtime.request(run_context=run, selection=frozenset()) as request:
            with pytest.raises(GraphError):
                await request.invoke_read_only(ref())
        assert handlers(system) == [("A", "r", "handler")]
    finally:
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_exact_preflight_refuses_run_before_authority_or_resources_retained(system):
    adapters = tuple(r for r in system[3].execution_adapters() if r.node_id != "w")
    coordinator, authority, runtime = await setup(system, adapters=adapters)
    runs = RunLeaseService(runtime)
    try:
        with pytest.raises(GraphError, match="adapter"):
            await runs.create(ttl=30)
        assert not authority.calls and not handlers(system)
        assert runs.counts == {"runs": 0, "pending": 0, "timers": 0}
    finally:
        runs.shutdown()
        await coordinator.shutdown()
    assert len(system[2].closed) == 4


@pytest.mark.asyncio
async def test_keeper_close_blocks_children_but_admitted_child_owns_resources(system):
    coordinator, _, _ = await setup(system)
    async with coordinator.request_lease() as lease:
        generation = lease.generation
        keeper = generation.retain()
    try:
        async with keeper.lease():
            binding_switch(system)
            await swap(system, coordinator)
            keeper.close()
            keeper.close()  # idempotent, no negative reference count
            assert ("A", "r") not in system[2].closed
            with pytest.raises(GraphError):
                async with keeper.lease():
                    pytest.fail("Closed keeper admitted child")
        await generation.retire()
        assert len(system[2].closed) == 4
    finally:
        keeper.close()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_injected_timer_failure_unwinds_create_and_renew_without_orphan(system):
    coordinator, _, runtime = await setup(system)
    timer = ManualTimer()
    clock = ManualClock()

    class FallibleTimer:
        fail = True

        def __call__(self, delay, callback):
            if self.fail:
                raise RuntimeError("Timer port unavailable")
            return timer(delay, callback)

    port = FallibleTimer()
    runs = RunLeaseService(runtime, clock=clock, timer=port)
    try:
        with pytest.raises(RuntimeError, match="Timer port"):
            await runs.create(ttl=5)
        assert runs.counts == {"runs": 0, "pending": 0, "timers": 0}
        port.fail = False
        run = await runs.create(ttl=5)
        port.fail = True
        with pytest.raises(RuntimeError, match="Timer port"):
            await runs.renew(run, ttl=5)
        assert runs.counts == {"runs": 0, "pending": 0, "timers": 0}
        assert all(handle.cancelled for _, _, handle in timer.handles)
    finally:
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_cancelled_run_request_auth_releases_child_after_keeper_release(system):
    coordinator, authority, runtime = await setup(system)
    runs = RunLeaseService(runtime)
    try:
        run = await runs.create(ttl=30)
        old = runs._validate(run).lease.generation
        binding_switch(system)
        await swap(system, coordinator)
        authority.block_operation = "request"

        async def blocked_request():
            async with runtime.request(run_context=run):
                pytest.fail("Cancelled request admitted")

        task = asyncio.create_task(blocked_request())
        await authority.entered.wait()
        runs.release(run)
        assert ("A", "r") not in system[2].closed
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await old.retire()
        assert old.resource_state == "retired" and len(system[2].closed) == 4
        assert not handlers(system)
    finally:
        runs.shutdown()
        await coordinator.shutdown()