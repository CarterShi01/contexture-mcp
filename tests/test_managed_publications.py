"""WP09 candidate evidence: official SDK memory streams and real FileJournal.

These tests establish bounded local behavior, not acceptance of spec/managed.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from unittest.mock import create_autospec, patch

from mcp.server import ServerRequestContext
from mcp.server.mcpserver import Context
from mcp.server.session import ServerSession
from mcp.shared.exceptions import MCPError
from mcp_types import (
    LATEST_PROTOCOL_VERSION, ReadResourceResult, RequestParamsMeta, TextResourceContents,
)
import pytest

from contexture.core.graph.bindings import RegistryGeneration
from contexture.core.graph.errors import GraphDiagnostic, GraphError
from contexture.server.managed import ManagedMCPServer
from tests.test_managed_mcp import payload, refused, session
from tests.test_managed_runtime import change, ref, setup, swap, definition, Execution, Provider
from tests.test_managed_owner import Host, owner, seed, SCHEMA, LATER
from contexture.application.managed_releases import (
    PROJECTION_NAME, release_projection, ReleaseIntentCoordinator,
)
from contexture.server.managed_journal import FileJournal
from contexture.application.managed_run_leases import RunLeaseService
from contexture.server.managed import RUN_META_KEY


@pytest.fixture
def system():
    with tempfile.TemporaryDirectory(prefix="contexture-publications-") as temp:
        with FileJournal.open(Path(temp) / "store", schema=SCHEMA) as journal:
            host, execution = Host(), Execution()
            provider = Provider(execution)
            provider.registrations = [replace(r, argument_free=True)
                if r.binding_id in ("r", "p", "reserved") and r.version == "A" else r
                for r in provider.registrations]
            provider.registry = RegistryGeneration(build="publications", registrations=provider.registrations)
            initial = definition(provider)
            contracts = {}
            for n, node in initial.nodes.items():
                assert node.binding is not None
                contracts[n] = provider.registry.resolve(node.binding).publication_contract
            manager = owner(journal, host, publication_contracts=contracts,
                projections={PROJECTION_NAME: release_projection()})
            envelope = seed()
            envelope["seed"]["inline"] = initial.as_json()
            manager.commit_bootstrap(manager.preview_bootstrap(envelope, expires_at=LATER))
            try:
                yield manager, host, execution, provider, ReleaseIntentCoordinator(manager, manifests=provider)
            finally:
                manager.close()


def resource(uri="ctxpub:read", target="r"):
    return {"kind": "resource", "publication_id": "resource-stable", "name": "read",
            "uri": uri, "mime_type": "application/json", "opens_id": target}


def publications(state):
    change(state, [
        {"op": "create_node", "node": {"node_id": "s", "kind": "skill", "name": "starter",
            "description": "Prompt-only instructions", "mutable": True,
            "instructions": "Start here; this does not execute Tools."}},
        {"op": "add_root", "node_id": "s", "plane": "prompt", "position": 4},
        {"op": "put_publication", "publication": {"kind": "prompt", "publication_id": "start-id",
            "name": "start", "description": "Start command", "opens_id": "s", "model_may_open": False}},
        {"op": "put_publication", "publication": resource()},
    ])


async def application(state):
    publications(state)
    coordinator, authority, runtime = await setup(state)
    authority.scope |= {"s"}
    return coordinator, authority, runtime


def text(result):
    return json.loads(result.messages[0].content.text)


def resource_value(result: ReadResourceResult):
    content = result.contents[0]
    assert isinstance(content, TextResourceContents)
    return json.loads(content.text)


@pytest.mark.asyncio
@pytest.mark.parametrize("modern", [True, False])
async def test_official_lists_get_read_reserved_and_no_notification_claim(system, modern):
    coordinator, authority, runtime = await application(system)
    try:
        async with session(ManagedMCPServer(runtime), modern=modern) as client:
            with patch.object(runtime, "request", wraps=runtime.request) as requests:
                prompts = await client.list_prompts()
                resources = await client.list_resources()
                command = await client.get_prompt("command")
                starter = await client.get_prompt("start")
                read = await client.read_resource("ctxpub:read")
                assert requests.call_count == 5
            assert [p.name for p in prompts.prompts] == ["command", "start"]
            assert prompts.ttl_ms == resources.ttl_ms == 0
            assert prompts.cache_scope == resources.cache_scope == "private"
            assert all(p.arguments == [] for p in prompts.prompts)
            assert resources.resources[0].uri == "ctxpub:read"
            assert resources.resources[0].meta is not None
            assert resources.resources[0].meta["ref"] == ref()
            assert text(command)["ref"] == ref("reserved")
            assert text(starter)["instructions"].startswith("Start here")
            assert resource_value(read)["version"] == "A"
            assert sum(c.operation == "request" for c in authority.calls) == 5
            assert system[2].calls == [("A", "r", step) for step in ("decoder", "validator", "handler")]
            caps = client.server_capabilities
            assert caps is not None
            assert caps.prompts is not None
            assert caps.resources is not None
            assert not caps.prompts.list_changed
            assert not caps.resources.list_changed and not caps.resources.subscribe
            assert len((await client.list_tools()).tools) == 5
            refused(await client.call_tool("contexture_open", {"ref": ref("reserved")}), "permission_denied")
            refused(await client.call_tool("contexture_open", {"ref": ref("s")}), "permission_denied")
            assert all(c["name"] != "starter" for c in payload(
                await client.call_tool("contexture_discover", {}))["skills"])
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_revocation_selection_and_exact_mapping_no_paths(system):
    coordinator, authority, runtime = await application(system)
    try:
        async with session(ManagedMCPServer(runtime)) as client:
            assert (await client.list_prompts()).prompts
            authority.scope = frozenset()
            assert not (await client.list_prompts()).prompts
            assert not (await client.list_resources()).resources
            for operation in (client.get_prompt("command"), client.read_resource("ctxpub:read")):
                with pytest.raises(MCPError, match="permission_denied"):
                    await operation
            authority.scope = frozenset({"r", "s", "reserved"})
            for name in ("reserved", "/reserved", ref("reserved"), "person-only"):
                with pytest.raises(MCPError, match="permission_denied"):
                    await client.get_prompt(name)
            with pytest.raises(MCPError, match="schema_invalid"):
                await client.get_prompt("command", {"actor_id": "forged"})
        async with runtime.request(selection=frozenset({"r"}), publication_audience=True) as request:
            assert await request.get_prompt() == {}
            with pytest.raises(GraphError):
                await request.get_prompt("person-only")
        assert system[2].calls == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_generation_swap_fresh_refs_and_old_uri_not_alias(system):
    coordinator, _, runtime = await application(system)
    try:
        async with session(ManagedMCPServer(runtime)) as client:
            before = (await client.list_resources()).resources[0]
            # Unique transaction key because the shared fixture uses "change".
            manager = system[0]
            envelope = {"schema": "contexture.transaction/v1", "graph_id": "g",
                "base_revision": manager.read("g").revision, "idempotency_key": "uri-change",
                "reason": "test", "correlation_id": "trace", "commands": [
                    {"op": "put_publication", "publication": resource("ctxpub:new")}]}
            from tests.test_managed_owner import LATER
            manager.commit(manager.preview(envelope, expires_at=LATER))
            await swap(system, coordinator)
            after = (await client.list_resources()).resources[0]
            assert before.meta is not None and after.meta is not None
            assert before.meta["ref"] == ref() and after.meta["ref"] == ref(generation=102)
            assert text(await client.get_prompt("command"))["ref"] == ref("reserved", generation=102)
            with pytest.raises(MCPError, match="permission_denied"):
                await client.read_resource("ctxpub:read")
            assert resource_value(await client.read_resource("ctxpub:new"))["version"] == "A"
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_publication_swap_during_authorization_keeps_one_pin(system):
    coordinator, authority, runtime = await application(system)
    try:
        async def change_generation():
            await authority.entered.wait()
            await swap(system, coordinator)
            authority.proceed.set()

        async with session(ManagedMCPServer(runtime)) as client:
            authority.block_operation = "request"
            changer = asyncio.create_task(change_generation())
            try:
                with patch.object(runtime, "request", wraps=runtime.request) as requests:
                    assert text(await client.get_prompt("start"))["ref"] == ref("s")
                    assert requests.call_count == 1
                await changer
            finally:
                if not changer.done():
                    changer.cancel()
            assert text(await client.get_prompt("start"))["ref"] == ref("s", generation=102)
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["graph", "unexpected"])
async def test_publication_failures_are_sanitized(system, failure, caplog):
    coordinator, authority, runtime = await application(system)
    try:
        async def fail(request):
            if failure == "graph":
                raise GraphError("permission_denied", "SECRET-token", location="/SECRET",
                    diagnostics=[GraphDiagnostic("schema_invalid", message="SECRET-global")])
            raise RuntimeError("SECRET-handler")

        async with session(ManagedMCPServer(runtime)) as client:
            with patch.object(authority, "verify", fail):
                for call in (client.list_prompts, client.list_resources,
                             lambda: client.get_prompt("command"), lambda: client.read_resource("ctxpub:read")):
                    with pytest.raises(MCPError) as error:
                        await call()
                    assert "SECRET" not in str(error.value)
            assert "SECRET" not in caplog.text and "Traceback" not in caplog.text
            assert system[2].calls == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["graph", "unexpected", "code"])
async def test_resource_handler_failure_is_sanitized_and_releases_request(system, failure, caplog):
    coordinator, _, runtime = await application(system)

    async def fail(*args, **kwargs):
        if failure == "graph":
            raise GraphError("resource_busy", "SECRET-resource", location="/SECRET-path")
        if failure == "code":
            raise GraphError("secret_resource_identifier", "SECRET-resource")
        raise RuntimeError("SECRET-handler")

    try:
        async with session(ManagedMCPServer(runtime)) as client:
            with patch("contexture.application.managed_runtime.PythonManagedCallAdapter.call", fail):
                with pytest.raises(MCPError) as error:
                    await client.read_resource("ctxpub:read")
            expected = "resource_busy" if failure == "graph" else "internal_error"
            assert expected in str(error.value)
            assert "SECRET" not in str(error.value)
            assert "secret_resource_identifier" not in str(error.value)
            assert "SECRET" not in caplog.text and "Traceback" not in caplog.text
            # A failed read does not poison the next SDK request or close owner handles.
            assert resource_value(await client.read_resource("ctxpub:read"))["version"] == "A"
            assert system[2].closed == []
    finally:
        await coordinator.shutdown()
    assert len(system[2].closed) == 4


@pytest.mark.asyncio
async def test_actual_oversize_list_refuses_without_truncation(system):
    manager = system[0]
    commands = [{"op": "put_publication", "publication": {
        "kind": "prompt", "publication_id": f"pub-{i}", "name": f"command-{i}",
        "description": "candidate", "opens_id": "reserved", "model_may_open": False}}
        for i in range(1001)]
    change(system, commands)
    coordinator, _, runtime = await setup(system)
    try:
        assert len(manager.read("g").definition.publications) == 1002
        async with session(ManagedMCPServer(runtime)) as client:
            with pytest.raises(MCPError, match="too_large"):
                await client.list_prompts()
            # Individual get remains bounded and is not dependent on list paging.
            assert text(await client.get_prompt("command-1000"))["ref"] == ref("reserved")
            with patch("contexture.server.managed_publications.MAX_RESPONSE_BYTES", 8):
                with pytest.raises(MCPError, match="too_large"):
                    await client.get_prompt("command-1000")
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["p", "reserved"])
async def test_resource_explicit_nonmodel_or_reserved_door_not_model_bypass(system, target):
    change(system, [{"op": "put_publication", "publication": resource(target=target)}])
    coordinator, authority, runtime = await setup(system)
    try:
        async with session(ManagedMCPServer(runtime)) as client:
            assert resource_value(await client.read_resource("ctxpub:read"))["version"] == "A"
            refused(await client.call_tool("contexture_invoke_read_only", {"ref": ref(target)}), "permission_denied")
            authority.scope = frozenset()
            with pytest.raises(MCPError, match="permission_denied"):
                await client.read_resource("ctxpub:read")
        assert [c for c in system[2].calls if c[2] == "handler"] == [("A", target, "handler")]
    finally:
        await coordinator.shutdown()


@pytest.mark.parametrize("target", ["w", "r"])
def test_forbidden_resource_effect_or_required_arguments_rejected_before_serving(system, target):
    # w is write; B's r requires count and has no argument-free attestation.
    if target == "r":
        from tests.test_managed_runtime import binding_switch
        binding_switch(system)
    with pytest.raises(GraphError):
        change(system, [{"op": "put_publication", "publication": resource(target=target)}])
    assert system[2].calls == []


@pytest.mark.asyncio
async def test_revoke_or_identity_change_between_mapping_and_action(system):
    coordinator, authority, runtime = await application(system)
    try:
        async with runtime.request(publication_audience=True) as request:
            assert "person-only" in await request.get_prompt()
            authority.scope = frozenset()
            with pytest.raises(GraphError):
                await request.get_prompt("person-only")
            with pytest.raises(GraphError):
                await request.read_resource("resource-stable")
        authority.scope = frozenset({"r", "s", "reserved"})
        async with runtime.request(publication_audience=True) as request:
            system[1].context = replace(system[1].context, actor_id="different")
            with pytest.raises(GraphError):
                await request.get_prompt("person-only")
        assert system[2].calls == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_cancelled_error_is_not_converted_to_protocol_failure(system):
    coordinator, _, runtime = await application(system)
    try:
        wire = ManagedMCPServer(runtime).build()
        async def cancel(*args):
            raise asyncio.CancelledError()
        context = ServerRequestContext(
            session=create_autospec(ServerSession, instance=True),
            lifespan_context={},
            protocol_version=LATEST_PROTOCOL_VERSION,
            method="prompts/list",
        )
        with patch.object(wire, "_publication_dispatch", cancel):
            with pytest.raises(asyncio.CancelledError):
                await wire._handle_list_prompts(context, None)
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["tool", "resource"])
@pytest.mark.parametrize("stage", ["request", "action", "handler"])
async def test_cancellation_inside_real_runtime_releases_host_request(system, door, stage):
    # Boundary test, not a claim about client cancellation notifications. Unlike
    # the dispatch-only test above, this enters real authorization and leases.
    coordinator, authority, runtime = await application(system)
    adapter = ManagedMCPServer(runtime)
    wire = adapter.build()
    context = ServerRequestContext(
        session=create_autospec(ServerSession, instance=True), lifespan_context={},
        protocol_version=LATEST_PROTOCOL_VERSION,
        method="tools/call" if door == "tool" else "resources/read",
    )
    original = authority.verify

    async def verify(request):
        operation = "request" if stage == "request" else "invoke_read_only"
        if stage != "handler" and request.operation == operation:
            raise asyncio.CancelledError()
        return await original(request)

    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError()

    try:
        with patch.object(authority, "verify", verify):
            with patch("contexture.application.managed_runtime.PythonManagedCallAdapter.call", cancel):
                with pytest.raises(asyncio.CancelledError):
                    if door == "tool":
                        await adapter._call(Context(request_context=context, mcp_server=wire),
                                            "invoke_read_only", ref(), {})
                    else:
                        await wire._publication(context, "read_resource", "ctxpub:read")
        assert system[2].calls == [] and system[2].closed == []
        async with session(adapter) as client:
            assert resource_value(await client.read_resource("ctxpub:read"))["version"] == "A"
    finally:
        await coordinator.shutdown()
    assert len(system[2].closed) == 4


@pytest.mark.asyncio
async def test_resource_revoke_during_final_authorization_never_dispatches(system):
    coordinator, authority, runtime = await application(system)
    try:
        async def revoke():
            await authority.entered.wait()
            authority.scope = frozenset()
            authority.proceed.set()

        async with session(ManagedMCPServer(runtime)) as client:
            authority.block_operation = "invoke_read_only"
            task = asyncio.create_task(revoke())
            try:
                with pytest.raises(MCPError, match="permission_denied"):
                    await client.read_resource("ctxpub:read")
                await task
            finally:
                if not task.done():
                    task.cancel()
        assert system[2].calls == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_host_selected_run_ceiling_is_not_widened_by_prompt_audience(system):
    coordinator, _, runtime = await application(system)
    runs = RunLeaseService(runtime)
    run = await runs.create(ttl=30, selection=frozenset({"r"}))
    class Resolver:
        async def resolve(self, context):
            return run
    try:
        async with session(ManagedMCPServer(runtime, run_context_resolver=Resolver())) as client:
            assert not (await client.list_prompts()).prompts
            assert len((await client.list_resources()).resources) == 1
            with pytest.raises(MCPError, match="permission_denied"):
                await client.get_prompt("command", meta=RequestParamsMeta({RUN_META_KEY: "host-routing"}))
            assert resource_value(await client.read_resource("ctxpub:read"))["version"] == "A"
            runs.release(run)
            with pytest.raises(MCPError, match="permission_denied"):
                await client.list_resources()
    finally:
        runs.shutdown()
        await coordinator.shutdown()