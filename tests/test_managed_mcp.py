"""WP09 candidate evidence through official ClientSession, never direct call_tool.

In-memory tests use the SDK's own stdio entry point with its transport patched
to official memory streams. One smoke test starts a real stdio subprocess.
This verifies a local candidate, not acceptance of a public managed wire schema.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp.shared.memory import create_client_server_memory_streams
from mcp_types import Prompt, RequestParamsMeta
import pytest

from contexture.application.managed_run_leases import RunLeaseService
from contexture.core.graph.errors import GraphDiagnostic, GraphError
from contexture.core.model.system_api import GATEWAY
from contexture.demo.server import build as static_build
from contexture.server.managed import ManagedMCPServer, RUN_META_KEY
from tests.test_managed_runtime import (
    binding_switch, ref, setup, swap, system as system,
)


@asynccontextmanager
async def session(adapter, *, modern=True):
    async with create_client_server_memory_streams() as (client, server):
        @asynccontextmanager
        async def memory_stdio():
            yield server

        # Public SDK lifecycle; no private request fields or fake handler calls.
        with patch("mcp.server.mcpserver.server.stdio_server", memory_stdio):
            async with anyio.create_task_group() as group:
                group.start_soon(adapter.build().run_stdio_async)
                try:
                    async with ClientSession(*client) as connected:
                        if modern:
                            await connected.discover()
                        else:
                            await connected.initialize()
                        yield connected
                finally:
                    group.cancel_scope.cancel()


def payload(result):
    assert not result.is_error
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def refused(result, code):
    assert result.is_error
    assert result.structured_content is None
    assert len(result.content) == 1
    text = result.content[0].text
    assert text.startswith("Error executing tool contexture_")
    assert text.split(": ", 1)[1] == f"{code}: Managed request refused"


def handlers(state):
    return [call for call in state[2].calls if call[2] == "handler"]


@pytest.mark.asyncio
@pytest.mark.parametrize("modern", [True, False])
async def test_real_gateway_navigation_execution_and_fixed_static_schema(system, modern):
    coordinator, authority, runtime = await setup(system)
    adapter = ManagedMCPServer(runtime)
    assert adapter.build() is adapter.build()
    try:
        async with session(static_build(), modern=modern) as client:
            old_tools = (await client.list_tools()).tools
        async with session(adapter, modern=modern) as client:
            tools = (await client.list_tools()).tools
            assert [tool.name for tool in tools] == [entry.name for entry in GATEWAY]
            for actual, old in zip(tools, old_tools, strict=True):
                assert actual.input_schema == old.input_schema
                assert actual.annotations == old.annotations
                assert actual.output_schema == old.output_schema
            assert client.instructions is not None
            assert "Managed profile candidate" in client.instructions
            assert "ctxg:v1" in client.instructions
            assert ref() not in client.instructions
            prompts = await client.list_prompts()
            resources = await client.list_resources()
            # The fixture declares only person-only -> reserved, not a Resource.
            assert prompts.prompts == [Prompt(name="command", description="User command",
                arguments=[], _meta={"ref": ref("reserved")})]
            assert resources.resources == []
            assert prompts.next_cursor is None and resources.next_cursor is None
            assert prompts.ttl_ms == resources.ttl_ms == 0
            assert prompts.cache_scope == resources.cache_scope == "private"
            caps = client.server_capabilities
            assert caps is not None
            assert caps.prompts is not None and caps.prompts.list_changed is False
            assert caps.resources is not None
            assert caps.resources.list_changed is False and caps.resources.subscribe is False
            command = await client.get_prompt("command")
            assert command.description == "User command"
            assert [message.role for message in command.messages] == ["user"]
            assert command.messages[0].content.type == "text"
            command_node = json.loads(command.messages[0].content.text)
            assert command_node["ref"] == ref("reserved")
            assert command_node["name"] == "reserved"
            assert command_node["read_only"] is True
            refused(await client.call_tool("contexture_open", {"ref": ref("reserved")}),
                    "permission_denied")
            with pytest.raises(MCPError, match="permission_denied"):
                await client.read_resource("ctxpub:read")
            assert system[2].calls == []  # Prompt disclosure never executes its Tool.
            publication_requests = sum(call.operation == "request" for call in authority.calls)
            # Two lists, one get, one denied model open, one undeclared URI read.
            assert publication_requests == 5
            roots = payload(await client.call_tool("contexture_discover", {}))
            assert [card["name"] for card in roots["tools"]] == ["r", "w", "reserved"]
            read = roots["tools"][0]["ref"]
            assert read == ref()
            assert "input_schema" not in roots["tools"][0]
            opened = await client.call_tool("contexture_open", {"ref": read})
            assert opened.structured_content["input_schema"]["properties"]["count"]
            inspected = payload(await client.call_tool("contexture_inspect", {"refs": [read]}))
            assert "input_schema" not in inspected["items"][0]["node"]
            result = await client.call_tool("contexture_invoke_read_only",
                                           {"ref": read, "arguments": {"count": "3"}})
            assert result.structured_content is None  # legacy gateway Any output
            assert payload(result)["count"] == 3
            assert payload(await client.call_tool("contexture_invoke", {"ref": ref("w")}))["version"] == "A"
            assert sum(call.operation == "request" for call in authority.calls) - publication_requests == 5
        # Closing MCP does NOT close the application-owned coordinator/resources.
        assert system[2].closed == []
        async with runtime.request() as request:
            assert (await request.discover())["tools"]
    finally:
        await coordinator.shutdown()
    assert len(system[2].closed) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,arguments,code", [
    ("contexture_invoke_read_only", {"ref": ref("w")}, "wrong_door"),
    ("contexture_invoke", {"ref": ref()}, "wrong_door"),
    ("contexture_open", {"ref": ref("p")}, "permission_denied"),
    ("contexture_open", {"ref": ref("reserved")}, "permission_denied"),
    ("contexture_invoke_read_only", {"ref": ref("p")}, "permission_denied"),
    ("contexture_invoke_read_only", {"ref": ref("reserved")}, "permission_denied"),
    ("contexture_inspect", {"refs": [ref(), ref("p")]}, "permission_denied"),
    ("contexture_inspect", {"refs": [ref(), ref()]}, "schema_invalid"),
    ("contexture_invoke_read_only", {"ref": ref(), "arguments": {"tenant_id": "forged"}}, "binding_contract_invalid"),
])
async def test_wire_refusal_is_atomic_and_never_dispatches(system, tool, arguments, code):
    coordinator, authority, runtime = await setup(system)
    try:
        async with session(ManagedMCPServer(runtime)) as client:
            refused(await client.call_tool(tool, arguments), code)
        assert handlers(system) == []
        assert system[2].calls == []
        assert sum(call.operation == "request" for call in authority.calls) == 1
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_swap_between_wire_calls_stale_and_fresh_schema(system):
    coordinator, _, runtime = await setup(system)
    try:
        async with session(ManagedMCPServer(runtime)) as client:
            before_tools = (await client.list_tools()).tools
            old = payload(await client.call_tool("contexture_discover", {}))["tools"][0]["ref"]
            binding_switch(system)
            await swap(system, coordinator)
            refused(await client.call_tool("contexture_open", {"ref": old}), "stale_reference")
            refused(await client.call_tool("contexture_invoke_read_only", {"ref": old}), "stale_reference")
            fresh = payload(await client.call_tool("contexture_discover", {}))["tools"][0]["ref"]
            assert fresh == ref(generation=102) and fresh != old
            schema = payload(await client.call_tool("contexture_open", {"ref": fresh}))["input_schema"]
            assert schema["required"] == ["count"]
            refused(await client.call_tool("contexture_invoke_read_only", {"ref": fresh}), "binding_contract_invalid")
            assert payload(await client.call_tool("contexture_invoke_read_only",
                {"ref": fresh, "arguments": {"count": 7}}))["version"] == "B"
            assert (await client.list_tools()).tools == before_tools
            assert handlers(system) == [("B", "r", "handler")]
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_swap_during_wire_authorization_keeps_one_request_pin(system):
    coordinator, authority, runtime = await setup(system)
    try:
        async def change_generation():
            await authority.entered.wait()
            binding_switch(system)
            await swap(system, coordinator)
            authority.proceed.set()

        async with session(ManagedMCPServer(runtime)) as client:
            authority.block_operation = "request"
            changer = asyncio.create_task(change_generation())
            try:
                with patch.object(runtime, "request", wraps=runtime.request) as requests:
                    result = await client.call_tool("contexture_invoke_read_only",
                        {"ref": ref(), "arguments": {"count": "6"}})
                    assert requests.call_count == 1
                await changer
            finally:
                if not changer.done():
                    changer.cancel()
            assert payload(result)["version"] == "A"
            # One request, but current-authority verification retries after swap.
            assert sum(call.operation == "request" for call in authority.calls) == 2
            assert authority.calls[-1].current_index.definition.nodes["r"].binding.version == "B"
            refused(await client.call_tool("contexture_open", {"ref": ref()}), "stale_reference")
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_trusted_per_request_meta_resolver_pins_old_run_and_rechecks_authority(system):
    coordinator, authority, runtime = await setup(system)
    runs = RunLeaseService(runtime)
    run = await runs.create(ttl=30, selection=frozenset({"r"}))

    class Resolver:
        def __init__(self):
            self.seen = []

        async def resolve(self, context: Context):
            meta = context.request_context.meta or {}
            self.seen.append(dict(meta))
            if RUN_META_KEY not in meta:
                return None
            # Local trusted Host mapping, not a public token schema or session.
            if meta[RUN_META_KEY] != "host-issued-routing-handle":
                raise GraphError("permission_denied", "secret-host-diagnostic")
            return run

    resolver = Resolver()
    meta = RequestParamsMeta({RUN_META_KEY: "host-issued-routing-handle"})
    try:
        binding_switch(system)
        await swap(system, coordinator)
        async with session(ManagedMCPServer(runtime, run_context_resolver=resolver)) as client:
            assert payload(await client.call_tool("contexture_discover", {}, meta=meta))["tools"][0]["ref"] == ref()
            assert payload(await client.call_tool("contexture_open", {"ref": ref()}, meta=meta))["input_schema"]["required"] == []
            assert payload(await client.call_tool("contexture_invoke_read_only",
                {"ref": ref(), "arguments": {"count": "6"}}, meta=meta))["version"] == "A"
            refused(await client.call_tool("contexture_open", {"ref": ref()}), "stale_reference")
            refused(await client.call_tool("contexture_discover", {},
                meta=RequestParamsMeta({RUN_META_KEY: "unknown"})), "permission_denied")
            authority.scope = frozenset()
            refused(await client.call_tool("contexture_invoke_read_only", {"ref": ref()}, meta=meta), "permission_denied")
            assert len(resolver.seen) == 6
            authority.scope = frozenset({"r"})
            runs.release(run)
            refused(await client.call_tool("contexture_open", {"ref": ref()}, meta=meta), "permission_denied")
            assert handlers(system) == [("A", "r", "handler")]
    finally:
        runs.shutdown()
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("meta", [{RUN_META_KEY: "secret"}, {"contexture/session": "secret"}])
async def test_unsupported_context_is_not_implicit_session(system, meta):
    coordinator, authority, runtime = await setup(system)
    try:
        async with session(ManagedMCPServer(runtime)) as client:
            refused(await client.call_tool("contexture_discover", {},
                meta=RequestParamsMeta(meta)), "unsupported_context")
        assert authority.calls == []
        assert handlers(system) == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["graph", "unexpected", "sdk", "code"])
async def test_host_failures_do_not_leak_diagnostics_tokens_or_tracebacks(system, failure, caplog):
    coordinator, _, runtime = await setup(system)

    class Resolver:
        async def resolve(self, context):
            if failure == "code":
                raise GraphError("secret_policy_identifier", "SECRET-token")
            if failure == "graph":
                raise GraphError("permission_denied", "SECRET-token", location="/SECRET-path",
                    diagnostics=[GraphDiagnostic("schema_invalid", message="SECRET-global")])
            if failure == "sdk":
                raise ToolError("SECRET-sdk")
            raise RuntimeError("SECRET-traceback")

    try:
        async with session(ManagedMCPServer(runtime, run_context_resolver=Resolver())) as client:
            result = await client.call_tool("contexture_discover", {})
            assert result.is_error and result.structured_content is None
            assert "SECRET" not in str(result)
            assert "secret_policy_identifier" not in str(result)
            assert "SECRET" not in caplog.text
            assert "Traceback" not in caplog.text
            expected = "internal_error" if failure in ("unexpected", "code") else "permission_denied"
            assert expected in str(result)
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_explicit_application_lifespan_is_respected_without_ownership(system):
    coordinator, _, runtime = await setup(system)
    marks = []

    @asynccontextmanager
    async def lifespan(wire):
        marks.append("enter")
        try:
            yield {"application": "owned"}
        finally:
            marks.append("exit")

    try:
        async with session(ManagedMCPServer(runtime, lifespan=lifespan)) as client:
            assert payload(await client.call_tool("contexture_discover", {}))["tools"]
            assert marks == ["enter"]
        assert marks == ["enter", "exit"]
        assert system[2].closed == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_one_real_stdio_subprocess_native_journal_smoke(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1")
    parameters = StdioServerParameters(command=sys.executable,
        args=["-B", "-m", "tests.managed_mcp_fixture"], env=env, cwd=str(root))
    stderr = tmp_path / "managed-stdio.stderr"
    with stderr.open("w") as errlog:
        with anyio.fail_after(45):
            async with stdio_client(parameters, errlog=errlog) as streams:
                async with ClientSession(*streams) as client:
                    await client.discover()
                    assert client.protocol_version == "2026-07-28"
                    assert client.instructions is not None
                    assert "Managed profile candidate" in client.instructions
                    assert len((await client.list_tools()).tools) == 5
                    roots = payload(await client.call_tool("contexture_discover", {}))
                    reference = roots["tools"][0]["ref"]
                    assert payload(await client.call_tool("contexture_open", {"ref": reference}))["read_only"]
                    assert payload(await client.call_tool("contexture_invoke_read_only",
                        {"ref": reference, "arguments": {"count": "8"}}))["count"] == 8
                    refused(await client.call_tool("contexture_invoke_read_only",
                        {"ref": roots["tools"][1]["ref"]}), "wrong_door")
    assert "Traceback" not in stderr.read_text()