"""Bounded WP09 application navigation through real serving and current policy."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json

import pytest

from contexture.application.managed_run_leases import RunLeaseService
from contexture.core.graph.errors import GraphError
from tests.test_managed_runtime import (
    binding_switch, change, ref, setup, swap, system,
)
from tests.test_managed_serving import uid


async def navigate(request, operation, reference=None):
    if operation == "discover":
        return await request.discover()
    if operation == "inspect":
        return await request.inspect([ref() if reference is None else reference])
    return await getattr(request, operation)(ref() if reference is None else reference)


@pytest.mark.asyncio
async def test_navigation_selection_hidden_errors_and_batch_snapshot(system):
    coordinator, authority, runtime = await setup(system)
    try:
        async with runtime.request(selection=frozenset({"reserved"})) as request:
            assert [c["name"] for c in (await request.discover())["tools"]] == ["reserved"]
            assert (await request.route(ref("reserved")))["name"] == "reserved"
            with pytest.raises(GraphError):
                await request.open(ref("reserved"))
            with pytest.raises(GraphError):
                await request.invoke_read_only(ref("reserved"))
        async with runtime.request(selection=frozenset()) as request:
            assert await request.discover() == {"roles": [], "skills": [], "tools": []}
        with pytest.raises(GraphError) as failure:
            async with runtime.request(selection=frozenset({"secret-anchor"})):
                pytest.fail("Unknown selection admitted")
        assert "secret-anchor" not in json.dumps(failure.value.to_dict())
        async with runtime.request(selection=frozenset({"r"})) as request:
            for operation in ("route", "inspect", "open"):
                for hidden in ("w", "p", "secret-target"):
                    with pytest.raises(GraphError) as failure:
                        await navigate(request, operation, ref(hidden))
                    assert failure.value.code == "permission_denied"
                    assert ref(hidden) not in json.dumps(failure.value.to_dict())
                    assert "secret-target" not in json.dumps(failure.value.to_dict())
                for invalid in ("/r", "r", ref(graph="other"), ref(generation=777)):
                    with pytest.raises(GraphError):
                        await navigate(request, operation, invalid)
            for invalid_batch in ([], [ref()] * 33, [ref(), ref()], "r"):
                with pytest.raises(GraphError):
                    await request.inspect(invalid_batch)
            references = [ref()]

            async def mutate():
                await authority.entered.wait()
                references[:] = [ref("w")]
                authority.proceed.set()

            authority.block_operation = "inspect"
            task = asyncio.create_task(mutate())
            assert (await request.inspect(references))["items"][0]["node"]["name"] == "r"
            await task
        assert system[2].calls == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["discover", "route", "inspect", "open"])
async def test_navigation_current_deletion_beats_lying_scope(system, operation):
    coordinator, authority, runtime = await setup(system)
    try:
        async def delete():
            await authority.entered.wait()
            change(system, [{"op": "delete_nodes", "targets": ["r"], "deleted_ids": ["r"],
                "policy": "reject_references", "disconnections": [
                    {"source_id": "g", "target_id": "r", "relation": "containment"}]}])
            await swap(system, coordinator)
            authority.proceed.set()

        async with runtime.request() as request:
            authority.block_operation = operation
            task = asyncio.create_task(delete())
            if operation == "discover":
                assert ref() not in json.dumps(await request.discover())
            else:
                with pytest.raises(GraphError) as failure:
                    await navigate(request, operation)
                assert failure.value.code == "permission_denied"
            await task
            assert "r" in authority.scope
        assert system[2].calls == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["discover", "route", "inspect", "open"])
async def test_navigation_identity_change_and_lifetime_guards(system, operation):
    coordinator, authority, runtime = await setup(system)
    try:
        async with runtime.request() as request:
            with pytest.raises(GraphError, match="another task"):
                await asyncio.create_task(navigate(request, operation))

            async def switch_identity():
                await authority.entered.wait()
                system[1].context = replace(system[1].context, tenant_id="other")
                authority.proceed.set()

            authority.block_operation = operation
            task = asyncio.create_task(switch_identity())
            with pytest.raises(GraphError, match="Authority changed"):
                await navigate(request, operation)
            await task
        with pytest.raises(GraphError, match="closed"):
            await navigate(request, operation)
        assert system[2].calls == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["discover", "route", "inspect", "open"])
async def test_navigation_run_release_during_auth_cannot_render(system, operation):
    coordinator, authority, runtime = await setup(system)
    service = RunLeaseService(runtime)
    try:
        envelope = await service.create(ttl=30)
        async with runtime.request(run_context=envelope) as request:
            async def release():
                await authority.entered.wait()
                service.release(envelope)
                authority.proceed.set()

            authority.block_operation = operation
            task = asyncio.create_task(release())
            with pytest.raises(GraphError, match="Run lease"):
                await navigate(request, operation)
            await task
        assert system[2].calls == []
    finally:
        service.shutdown()
        await coordinator.shutdown()
@pytest.mark.asyncio
async def test_navigation_real_disclosure_separates_route_from_activation(system):
    coordinator, authority, runtime = await setup(system)
    try:
        async with runtime.request() as request:
            cards = (await request.discover())["tools"]
            assert [card["name"] for card in cards] == ["r", "w", "reserved"]
            assert all(set(card) == {"kind", "name", "description", "ref"} for card in cards)
            assert await request.route(ref("reserved")) == cards[-1]
            inspected = await request.inspect([ref("w"), ref()])
            assert [item["node"]["name"] for item in inspected["items"]] == ["w", "r"]
            assert "input_schema" not in json.dumps(inspected)
            opened = await request.open(ref())
            assert opened["read_only"] is True and "input_schema" in opened
            opened["input_schema"]["properties"].clear()
            assert (await request.open(ref()))["input_schema"]["properties"]
            for operation in ("open", "inspect", "invoke_read_only"):
                with pytest.raises(GraphError):
                    if operation == "invoke_read_only":
                        await request.invoke_read_only(ref("reserved"))
                    else:
                        await navigate(request, operation, ref("reserved"))
        assert [call.operation for call in authority.calls][:5] == [
            "request", "discover", "route", "inspect", "open"]
        assert "reserved" in authority.calls[0].requested_ids
        assert system[2].calls == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["discover", "route", "inspect", "open"])
@pytest.mark.parametrize("during_await", [False, True])
async def test_navigation_current_revocation_and_initial_ceiling(system, operation, during_await):
    coordinator, authority, runtime = await setup(system)
    try:
        authority.scope = frozenset({"r"})
        async with runtime.request() as request:
            authority.scope = frozenset({"r", "w", "reserved"})
            assert [c["name"] for c in (await request.discover())["tools"]] == ["r"]
            with pytest.raises(GraphError):
                await request.route(ref("w"))

            async def revoke():
                await authority.entered.wait()
                authority.scope = frozenset()
                authority.proceed.set()

            task = None
            if during_await:
                authority.block_operation = operation
                task = asyncio.create_task(revoke())
            else:
                authority.scope = frozenset()
            if operation == "discover":
                assert await request.discover() == {"roles": [], "skills": [], "tools": []}
            else:
                with pytest.raises(GraphError) as failure:
                    await navigate(request, operation)
                assert failure.value.code == "permission_denied"
            if task:
                await task
        assert system[2].calls == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["discover", "route", "inspect", "open"])
async def test_navigation_swap_during_policy_keeps_pinned_generation(system, operation):
    coordinator, authority, runtime = await setup(system)
    try:
        async def release_new():
            await authority.entered.wait()
            binding_switch(system)
            await swap(system, coordinator)
            authority.proceed.set()

        async with runtime.request() as request:
            authority.block_operation = operation
            task = asyncio.create_task(release_new())
            result = await navigate(request, operation)
            await task
            assert uid(101) in json.dumps(result) and uid(102) not in json.dumps(result)
            assert request.generation_uuid == uid(101)
            assert authority.calls[-1].current_index.definition.nodes["r"].binding.version == "B"
            assert ("A", "r") not in system[2].closed
            assert (await request.open(ref()))["input_schema"]["properties"]["count"]["type"] == [
                "integer", "string"]
        async with runtime.request() as request:
            for action in ("route", "inspect", "open"):
                with pytest.raises(GraphError) as failure:
                    await navigate(request, action)
                assert failure.value.code == "stale_reference"
            assert (await request.open(ref(generation=102)))["input_schema"]["properties"]["count"]["type"] == "integer"
        assert system[2].calls == []
    finally:
        await coordinator.shutdown()